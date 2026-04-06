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

# ── Jurisdiction / Country synonym mapping ─────────────────────────
# Maps alternate names, abbreviations, and sub-jurisdictions to a
# canonical ISO 3166-1 form.  Used by DOC-07 jurisdiction match and
# country-risk scoring to eliminate false positives/negatives caused
# by the old 3-character prefix comparison.

_ORDINAL_SUFFIX_RE = re.compile(r'(\d+)(st|nd|rd|th)\b', re.IGNORECASE)

_JURISDICTION_SYNONYMS: Dict[str, str] = {
    # United Kingdom variants
    "uk": "united kingdom", "gb": "united kingdom", "gbr": "united kingdom",
    "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom", "scotland": "united kingdom",
    "wales": "united kingdom", "northern ireland": "united kingdom",
    "england and wales": "united kingdom", "england & wales": "united kingdom",
    # United States variants
    "us": "united states", "usa": "united states",
    "united states of america": "united states",
    # Mauritius variants
    "mu": "mauritius", "republic of mauritius": "mauritius",
    "mauritian": "mauritius",
    # Common country aliases
    "uae": "united arab emirates", "emirates": "united arab emirates",
    "korea": "south korea", "republic of korea": "south korea",
    "rok": "south korea", "dprk": "north korea",
    "prc": "china", "peoples republic of china": "china",
    "people's republic of china": "china",
    "bvi": "british virgin islands",
    "hk": "hong kong", "sar": "hong kong",
    "sg": "singapore", "ch": "switzerland",
    "de": "germany", "fr": "france", "nl": "netherlands",
    "ie": "ireland", "republic of ireland": "ireland",
    "nz": "new zealand", "au": "australia", "ca": "canada",
    "jp": "japan", "za": "south africa",
    "isle of man": "isle of man", "guernsey": "guernsey", "jersey": "jersey",
}


def _canonicalise_jurisdiction(name: str) -> str:
    """Resolve country/jurisdiction to a canonical lowercase form."""
    if not name:
        return ""
    n = _normalise_name(name)
    return _JURISDICTION_SYNONYMS.get(n, n)


# ── Nationality ↔ Country mapping (demonyms + ISO codes) ──────────
# Maps demonyms (e.g. "mauritian") and ISO alpha-2 codes to canonical
# country names, enabling DOC-52/DOC-56 to correctly compare a
# passport-declared nationality against a pre-screening country.

_NATIONALITY_TO_COUNTRY: Dict[str, str] = {
    # ISO alpha-2 codes
    "gb": "united kingdom", "us": "united states", "mu": "mauritius",
    "fr": "france", "de": "germany", "in": "india", "cn": "china",
    "za": "south africa", "sg": "singapore", "ae": "united arab emirates",
    "au": "australia", "ca": "canada", "nz": "new zealand", "jp": "japan",
    "kr": "south korea", "ie": "ireland", "nl": "netherlands",
    "ch": "switzerland", "it": "italy", "es": "spain", "pt": "portugal",
    "se": "sweden", "no": "norway", "dk": "denmark", "fi": "finland",
    "be": "belgium", "lu": "luxembourg", "at": "austria", "hk": "hong kong",
    # Demonyms
    "british": "united kingdom", "english": "united kingdom",
    "scottish": "united kingdom", "welsh": "united kingdom",
    "american": "united states", "mauritian": "mauritius",
    "french": "france", "german": "germany", "indian": "india",
    "chinese": "china", "south african": "south africa",
    "singaporean": "singapore", "emirati": "united arab emirates",
    "australian": "australia", "canadian": "canada",
    "new zealander": "new zealand", "japanese": "japan",
    "korean": "south korea", "irish": "ireland", "dutch": "netherlands",
    "swiss": "switzerland", "italian": "italy", "spanish": "spain",
    "portuguese": "portugal", "swedish": "sweden", "norwegian": "norway",
    "danish": "denmark", "finnish": "finland", "belgian": "belgium",
    "luxembourgish": "luxembourg", "austrian": "austria",
    "russian": "russia", "brazilian": "brazil", "mexican": "mexico",
    "nigerian": "nigeria", "kenyan": "kenya", "ghanaian": "ghana",
    "egyptian": "egypt", "turkish": "turkey", "saudi": "saudi arabia",
    "pakistani": "pakistan", "bangladeshi": "bangladesh",
    "sri lankan": "sri lanka", "thai": "thailand", "vietnamese": "vietnam",
    "filipino": "philippines", "indonesian": "indonesia",
    "malaysian": "malaysia",
}


def _canonicalise_nationality(name: str) -> str:
    """Resolve a nationality string (demonym, ISO code, or country name) to canonical country."""
    if not name:
        return ""
    n = _normalise_name(name)
    # Check demonym / ISO lookup first
    resolved = _NATIONALITY_TO_COUNTRY.get(n)
    if resolved:
        return resolved
    # Fall through to jurisdiction synonyms (handles "republic of mauritius" etc.)
    return _canonicalise_jurisdiction(n)
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


# ── Country / Nationality canonical mapping ──────────────────────
# Maps common variations (full names, demonyms, ISO codes) to a single
# canonical key for reliable comparison in DOC-07 / DOC-52 / DOC-56.

_COUNTRY_CANONICAL = {}  # populated below from _COUNTRY_ENTRIES

_COUNTRY_ENTRIES = [
    ("AF", ["afghanistan", "afghan", "afg"]),
    ("AL", ["albania", "albanian", "alb"]),
    ("DZ", ["algeria", "algerian", "dza"]),
    ("AD", ["andorra", "andorran", "and"]),
    ("AO", ["angola", "angolan", "ago"]),
    ("AG", ["antigua and barbuda", "antiguan", "barbudan", "atg"]),
    ("AR", ["argentina", "argentine", "argentinian", "arg"]),
    ("AM", ["armenia", "armenian", "arm"]),
    ("AU", ["australia", "australian", "aus"]),
    ("AT", ["austria", "austrian", "aut"]),
    ("AZ", ["azerbaijan", "azerbaijani", "aze"]),
    ("BS", ["bahamas", "bahamian", "bhs"]),
    ("BH", ["bahrain", "bahraini", "bhr"]),
    ("BD", ["bangladesh", "bangladeshi", "bgd"]),
    ("BB", ["barbados", "barbadian", "bajan", "brb"]),
    ("BY", ["belarus", "belarusian", "blr"]),
    ("BE", ["belgium", "belgian", "bel"]),
    ("BZ", ["belize", "belizean", "blz"]),
    ("BJ", ["benin", "beninese", "ben"]),
    ("BT", ["bhutan", "bhutanese", "btn"]),
    ("BO", ["bolivia", "bolivian", "bol"]),
    ("BA", ["bosnia and herzegovina", "bosnian", "herzegovinian", "bih"]),
    ("BW", ["botswana", "motswana", "batswana", "bwa"]),
    ("BR", ["brazil", "brazilian", "bra"]),
    ("BN", ["brunei", "bruneian", "brn"]),
    ("BG", ["bulgaria", "bulgarian", "bgr"]),
    ("BF", ["burkina faso", "burkinabe", "bfa"]),
    ("BI", ["burundi", "burundian", "bdi"]),
    ("CV", ["cabo verde", "cape verde", "cape verdean", "cpv"]),
    ("KH", ["cambodia", "cambodian", "khm"]),
    ("CM", ["cameroon", "cameroonian", "cmr"]),
    ("CA", ["canada", "canadian", "can"]),
    ("CF", ["central african republic", "central african", "caf"]),
    ("TD", ["chad", "chadian", "tcd"]),
    ("CL", ["chile", "chilean", "chl"]),
    ("CN", ["china", "chinese", "peoples republic of china", "prc", "chn"]),
    ("CO", ["colombia", "colombian", "col"]),
    ("KM", ["comoros", "comorian", "com"]),
    ("CG", ["congo", "congolese", "republic of the congo", "cog"]),
    ("CD", ["democratic republic of the congo", "drc", "dr congo", "cod"]),
    ("CR", ["costa rica", "costa rican", "cri"]),
    ("CI", ["cote d ivoire", "ivory coast", "ivorian", "civ"]),
    ("HR", ["croatia", "croatian", "hrv"]),
    ("CU", ["cuba", "cuban", "cub"]),
    ("CY", ["cyprus", "cypriot", "cyp"]),
    ("CZ", ["czech republic", "czechia", "czech", "cze"]),
    ("DK", ["denmark", "danish", "dane", "dnk"]),
    ("DJ", ["djibouti", "djiboutian", "dji"]),
    ("DM", ["dominica", "dominican", "dma"]),
    ("DO", ["dominican republic", "dom"]),
    ("EC", ["ecuador", "ecuadorian", "ecu"]),
    ("EG", ["egypt", "egyptian", "egy"]),
    ("SV", ["el salvador", "salvadoran", "slv"]),
    ("GQ", ["equatorial guinea", "equatoguinean", "gnq"]),
    ("ER", ["eritrea", "eritrean", "eri"]),
    ("EE", ["estonia", "estonian", "est"]),
    ("SZ", ["eswatini", "swaziland", "swazi", "swz"]),
    ("ET", ["ethiopia", "ethiopian", "eth"]),
    ("FJ", ["fiji", "fijian", "fji"]),
    ("FI", ["finland", "finnish", "finn", "fin"]),
    ("FR", ["france", "french", "fra"]),
    ("GA", ["gabon", "gabonese", "gab"]),
    ("GM", ["gambia", "gambian", "gmb"]),
    ("GE", ["georgia", "georgian", "geo"]),
    ("DE", ["germany", "german", "deu"]),
    ("GH", ["ghana", "ghanaian", "gha"]),
    ("GR", ["greece", "greek", "grc"]),
    ("GD", ["grenada", "grenadian", "grd"]),
    ("GT", ["guatemala", "guatemalan", "gtm"]),
    ("GN", ["guinea", "guinean", "gin"]),
    ("GW", ["guinea-bissau", "guinea bissau", "bissau-guinean", "gnb"]),
    ("GY", ["guyana", "guyanese", "guy"]),
    ("HT", ["haiti", "haitian", "hti"]),
    ("HN", ["honduras", "honduran", "hnd"]),
    ("HK", ["hong kong", "hkg"]),
    ("HU", ["hungary", "hungarian", "hun"]),
    ("IS", ["iceland", "icelandic", "icelander", "isl"]),
    ("IN", ["india", "indian", "ind"]),
    ("ID", ["indonesia", "indonesian", "idn"]),
    ("IR", ["iran", "iranian", "irn"]),
    ("IQ", ["iraq", "iraqi", "irq"]),
    ("IE", ["ireland", "irish", "irl"]),
    ("IL", ["israel", "israeli", "isr"]),
    ("IT", ["italy", "italian", "ita"]),
    ("JM", ["jamaica", "jamaican", "jam"]),
    ("JP", ["japan", "japanese", "jpn"]),
    ("JO", ["jordan", "jordanian", "jor"]),
    ("KZ", ["kazakhstan", "kazakh", "kaz"]),
    ("KE", ["kenya", "kenyan", "ken"]),
    ("KI", ["kiribati", "i-kiribati", "kir"]),
    ("KP", ["north korea", "dprk", "prk"]),
    ("KR", ["south korea", "korea", "korean", "kor"]),
    ("KW", ["kuwait", "kuwaiti", "kwt"]),
    ("KG", ["kyrgyzstan", "kyrgyz", "kgz"]),
    ("LA", ["laos", "lao"]),
    ("LV", ["latvia", "latvian", "lva"]),
    ("LB", ["lebanon", "lebanese", "lbn"]),
    ("LS", ["lesotho", "basotho", "lso"]),
    ("LR", ["liberia", "liberian", "lbr"]),
    ("LY", ["libya", "libyan", "lby"]),
    ("LI", ["liechtenstein", "lie"]),
    ("LT", ["lithuania", "lithuanian", "ltu"]),
    ("LU", ["luxembourg", "luxembourgish", "lux"]),
    ("MO", ["macao", "macau", "mac"]),
    ("MG", ["madagascar", "malagasy", "mdg"]),
    ("MW", ["malawi", "malawian", "mwi"]),
    ("MY", ["malaysia", "malaysian", "mys"]),
    ("MV", ["maldives", "maldivian", "mdv"]),
    ("ML", ["mali", "malian", "mli"]),
    ("MT", ["malta", "maltese", "mlt"]),
    ("MH", ["marshall islands", "marshallese", "mhl"]),
    ("MR", ["mauritania", "mauritanian", "mrt"]),
    ("MU", ["mauritius", "mauritian", "mus"]),
    ("MX", ["mexico", "mexican", "mex"]),
    ("FM", ["micronesia", "micronesian", "fsm"]),
    ("MD", ["moldova", "moldovan", "mda"]),
    ("MC", ["monaco", "monacan", "monegasque", "mco"]),
    ("MN", ["mongolia", "mongolian", "mng"]),
    ("ME", ["montenegro", "montenegrin", "mne"]),
    ("MA", ["morocco", "moroccan", "mar"]),
    ("MZ", ["mozambique", "mozambican", "moz"]),
    ("MM", ["myanmar", "burmese", "burma", "mmr"]),
    ("NA", ["namibia", "namibian", "nam"]),
    ("NR", ["nauru", "nauruan", "nru"]),
    ("NP", ["nepal", "nepalese", "nepali", "npl"]),
    ("NL", ["netherlands", "dutch", "holland", "nld"]),
    ("NZ", ["new zealand", "new zealander", "kiwi", "nzl"]),
    ("NI", ["nicaragua", "nicaraguan", "nic"]),
    ("NE", ["niger", "nigerien", "ner"]),
    ("NG", ["nigeria", "nigerian", "nga"]),
    ("MK", ["north macedonia", "macedonia", "macedonian", "mkd"]),
    ("NO", ["norway", "norwegian", "nor"]),
    ("OM", ["oman", "omani", "omn"]),
    ("PK", ["pakistan", "pakistani", "pak"]),
    ("PW", ["palau", "palauan", "plw"]),
    ("PS", ["palestine", "palestinian", "pse"]),
    ("PA", ["panama", "panamanian", "pan"]),
    ("PG", ["papua new guinea", "papua new guinean", "png"]),
    ("PY", ["paraguay", "paraguayan", "pry"]),
    ("PE", ["peru", "peruvian", "per"]),
    ("PH", ["philippines", "filipino", "philippine", "phl"]),
    ("PL", ["poland", "polish", "pol"]),
    ("PT", ["portugal", "portuguese", "prt"]),
    ("QA", ["qatar", "qatari", "qat"]),
    ("RO", ["romania", "romanian", "rou"]),
    ("RU", ["russia", "russian", "russian federation", "rus"]),
    ("RW", ["rwanda", "rwandan", "rwa"]),
    ("KN", ["saint kitts and nevis", "kittitian", "nevisian", "kna"]),
    ("LC", ["saint lucia", "saint lucian", "lca"]),
    ("VC", ["saint vincent and the grenadines", "vincentian", "vct"]),
    ("WS", ["samoa", "samoan", "wsm"]),
    ("SM", ["san marino", "sammarinese", "smr"]),
    ("ST", ["sao tome and principe", "stp"]),
    ("SA", ["saudi arabia", "saudi", "sau"]),
    ("SN", ["senegal", "senegalese", "sen"]),
    ("RS", ["serbia", "serbian", "srb"]),
    ("SC", ["seychelles", "seychellois", "syc"]),
    ("SL", ["sierra leone", "sierra leonean", "sle"]),
    ("SG", ["singapore", "singaporean", "sgp"]),
    ("SK", ["slovakia", "slovak", "svk"]),
    ("SI", ["slovenia", "slovenian", "svn"]),
    ("SB", ["solomon islands", "slb"]),
    ("SO", ["somalia", "somali", "som"]),
    ("ZA", ["south africa", "south african", "zaf"]),
    ("SS", ["south sudan", "south sudanese", "ssd"]),
    ("ES", ["spain", "spanish", "esp"]),
    ("LK", ["sri lanka", "sri lankan", "lka"]),
    ("SD", ["sudan", "sudanese", "sdn"]),
    ("SR", ["suriname", "surinamese", "sur"]),
    ("SE", ["sweden", "swedish", "swede", "swe"]),
    ("CH", ["switzerland", "swiss", "che"]),
    ("SY", ["syria", "syrian", "syr"]),
    ("TW", ["taiwan", "taiwanese", "twn"]),
    ("TJ", ["tajikistan", "tajik", "tjk"]),
    ("TZ", ["tanzania", "tanzanian", "tza"]),
    ("TH", ["thailand", "thai", "tha"]),
    ("TL", ["timor-leste", "east timor", "timorese", "tls"]),
    ("TG", ["togo", "togolese", "tgo"]),
    ("TO", ["tonga", "tongan", "ton"]),
    ("TT", ["trinidad and tobago", "trinidadian", "tobagonian", "tto"]),
    ("TN", ["tunisia", "tunisian", "tun"]),
    ("TR", ["turkey", "turkiye", "turkish", "tur"]),
    ("TM", ["turkmenistan", "turkmen", "tkm"]),
    ("TV", ["tuvalu", "tuvaluan", "tuv"]),
    ("UG", ["uganda", "ugandan", "uga"]),
    ("UA", ["ukraine", "ukrainian", "ukr"]),
    ("AE", ["united arab emirates", "uae", "emirati", "are"]),
    ("GB", ["united kingdom", "uk", "british", "great britain", "england", "scotland", "wales", "northern ireland", "gbr"]),
    ("US", ["united states", "usa", "us", "american", "united states of america"]),
    ("UY", ["uruguay", "uruguayan", "ury"]),
    ("UZ", ["uzbekistan", "uzbek", "uzb"]),
    ("VU", ["vanuatu", "ni-vanuatu", "vut"]),
    ("VA", ["vatican city", "vatican", "vat"]),
    ("VE", ["venezuela", "venezuelan", "ven"]),
    ("VN", ["vietnam", "vietnamese", "viet nam", "vnm"]),
    ("YE", ["yemen", "yemeni", "yem"]),
    ("ZM", ["zambia", "zambian", "zmb"]),
    ("ZW", ["zimbabwe", "zimbabwean", "zwe"]),
    # Overseas territories / special entities
    ("VG", ["british virgin islands", "bvi", "vgb"]),
    ("KY", ["cayman islands", "caymanian", "cym"]),
    ("BM", ["bermuda", "bermudian", "bmu"]),
    ("GI", ["gibraltar", "gibraltarian", "gib"]),
    ("JE", ["jersey", "jey"]),
    ("GG", ["guernsey", "ggy"]),
    ("IM", ["isle of man", "manx", "imn"]),
    ("CW", ["curacao", "cuw"]),
    ("PR", ["puerto rico", "puerto rican", "pri"]),
    ("GU", ["guam", "guamanian", "gum"]),
    ("AS", ["american samoa", "asm"]),
    ("VI", ["us virgin islands", "vir"]),
    ("TC", ["turks and caicos islands", "turks and caicos", "tca"]),
]

def _build_country_canonical():
    """Build the reverse-lookup from variations to canonical ISO code."""
    for iso_code, variants in _COUNTRY_ENTRIES:
        code_lower = iso_code.lower()
        _COUNTRY_CANONICAL[code_lower] = iso_code
        for var in variants:
            _COUNTRY_CANONICAL[var.lower()] = iso_code

_build_country_canonical()


def _canonicalise_country(value: str) -> str:
    """Resolve a country name, nationality, or ISO code to a canonical ISO 3166-1 alpha-2 code.
    Returns empty string if no match found.
    """
    if not value:
        return ""
    normed = re.sub(r"[.,'\-]", " ", value.lower())
    normed = re.sub(r"\s+", " ", normed).strip()
    # Direct lookup
    canon = _COUNTRY_CANONICAL.get(normed)
    if canon:
        return canon
    # Try 2-char or 3-char code match
    if len(normed) <= 3:
        canon = _COUNTRY_CANONICAL.get(normed)
        if canon:
            return canon
    # Fuzzy: try stripping common suffixes like "republic of ..."
    for prefix in ("republic of ", "the ", "state of "):
        if normed.startswith(prefix):
            canon = _COUNTRY_CANONICAL.get(normed[len(prefix):])
            if canon:
                return canon
    return ""


def _countries_match(a: str, b: str) -> bool:
    """Return True if two country/nationality/jurisdiction values resolve to the same canonical code."""
    ca = _canonicalise_country(a)
    cb = _canonicalise_country(b)
    if ca and cb:
        return ca == cb
    # Fallback: normalised full string comparison
    na = _normalise_name(a)
    nb = _normalise_name(b)
    return na == nb and na != ""

def _normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not name:
        return ""
    n = name.lower()
    n = re.sub(r"[.,'\-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


# ── Address abbreviation expansion ─────────────────────────────────
_ADDRESS_ABBREVIATIONS: Dict[str, str] = {
    "st": "street", "rd": "road", "ave": "avenue", "dr": "drive",
    "blvd": "boulevard", "ct": "court", "pl": "place", "ln": "lane",
    "cres": "crescent", "tce": "terrace", "hwy": "highway",
    "sq": "square", "pk": "park", "grn": "green", "bldg": "building",
    "ste": "suite", "apt": "apartment", "fl": "floor",
}


def _expand_address_abbreviations(text: str) -> str:
    """Expand common address abbreviations for better matching.

    '10 Downing St' → '10 Downing street'
    """
    if not text:
        return ""
    words = _normalise_name(text).split()
    expanded = []
    for w in words:
        expanded.append(_ADDRESS_ABBREVIATIONS.get(w, w))
    return " ".join(expanded)


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
    Returns 0.0–1.0.  Also expands common address abbreviations
    (St→Street, Rd→Road etc.) to reduce false negatives on addresses.
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
    # Try with address abbreviation expansion
    a_exp = _expand_address_abbreviations(a)
    b_exp = _expand_address_abbreviations(b)
    if a_exp == b_exp:
        return 1.0

    def trigrams(s):
        s = " " + s + " "
        return {s[i:i+3] for i in range(len(s) - 2)}

    # Use expanded forms for trigram calculation to improve address matching
    tg_a = trigrams(a_exp)
    tg_b = trigrams(b_exp)
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
    """Try several common date formats, return date or None.

    Handles:
    - ordinals: "4th March 2026" → "4 March 2026"
    - 2-digit years: "04/03/26" → interprets as 20XX (window 2000-2099)
    - multiple common date formats
    """
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    s = str(val).strip()
    if not s:
        return None
    # Strip ordinal suffixes: 1st, 2nd, 3rd, 4th, 21st, etc.
    s = _ORDINAL_SUFFIX_RE.sub(r'\1', s)
    # Try standard formats first
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y",
                "%d-%m-%Y", "%B %d, %Y", "%d %b %Y", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # 2-digit year formats: dd/mm/yy, dd-mm-yy
    for fmt in ("%d/%m/%y", "%d-%m-%y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
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
            logger.debug("DOC-06 Registration Number: field_name=registration_number, "
                         "declared_present=%s, extracted_present=%s",
                         bool(declared), bool(extracted))
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
            # Normalise: strip spaces, hyphens, and leading zeros for comparison
            d_norm = re.sub(r"[\s\-]", "", str(declared).upper()).lstrip("0") or "0"
            e_norm = re.sub(r"[\s\-]", "", str(extracted).upper()).lstrip("0") or "0"
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
            logger.debug("DOC-49A Date of Birth: field_name=date_of_birth, "
                         "declared_present=%s, extracted_present=%s",
                         bool(declared_dob), bool(extracted_dob))
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
            if not d_d or not d_e:
                # Cannot parse one or both dates — never silently pass
                results.append(_warn(id_, label, cls,
                                     f"Date of birth could not be parsed for comparison "
                                     f"(declared: '{declared_dob}' → {d_d}, "
                                     f"extracted: '{extracted_dob}' → {d_e}) — manual check required",
                                     ps_field=PSField.PERSON_DOB,
                                     ps_value=str(declared_dob), extracted_value=str(extracted_dob),
                                     rule_type=rtype))
            elif d_d == d_e:
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
            logger.debug("%s Nationality: field_name=nationality, "
                         "declared_present=%s, extracted_present=%s",
                         id_, bool(declared_nat), bool(extracted_nat))
            if not declared_nat or not extracted_nat:
                results.append(_warn(id_, label, cls,
                                     "Nationality not extractable or not declared — manual check required",
                                     rule_type=rtype))
                continue
            # Canonical country/nationality comparison (replaces broken prefix logic)
            if _countries_match(declared_nat, extracted_nat):
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
            # Compare declared vs extracted shareholding percentages by name match
            mismatches = []
            matched = 0
            for declared in declared_shareholders:
                d_name = declared.get("full_name", "") or f"{declared.get('first_name', '')} {declared.get('last_name', '')}".strip()
                d_pct = declared.get("ownership_pct") or declared.get("percentage")
                if not d_name:
                    continue
                best_match = None
                best_sim = 0
                for ext in extracted_holders:
                    e_name = ext.get("name", "") or ext.get("full_name", "")
                    sim = _name_similarity(d_name, e_name) if d_name and e_name else 0
                    if sim > best_sim:
                        best_sim = sim
                        best_match = ext
                if best_match and best_sim >= NAME_MATCH_WARN_THRESHOLD:
                    matched += 1
                    e_pct = best_match.get("percentage") or best_match.get("ownership_pct")
                    if d_pct is not None and e_pct is not None:
                        try:
                            if abs(float(d_pct) - float(e_pct)) > 1.0:
                                mismatches.append(f"{d_name}: declared {d_pct}%, register {e_pct}%")
                        except (ValueError, TypeError):
                            pass
            if mismatches:
                results.append(_fail(id_, label, cls,
                                     f"Shareholding percentage mismatch: {'; '.join(mismatches)}",
                                     ps_field=PSField.SHAREHOLDERS,
                                     rule_type=rtype))
            else:
                results.append(_pass(id_, label, cls,
                                     f"Shareholding percentages verified for {matched}/{len(declared_shareholders)} declared holders",
                                     ps_field=PSField.SHAREHOLDERS,
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
            declared_shareholders = ps_get(PSField.SHAREHOLDERS, "shareholders", "ubos") or []
            extracted_entities = ef.get("entities", ef.get("shareholders", []))
            if not declared_shareholders:
                results.append(_warn(id_, label, cls,
                                     "No declared shareholders/UBOs to compare against structure chart",
                                     rule_type=rtype))
                continue
            if not extracted_entities:
                results.append(_warn(id_, label, cls,
                                     "Structure chart entities could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            mismatches = []
            matched = 0
            for declared in declared_shareholders:
                d_name = declared.get("full_name", "") or f"{declared.get('first_name', '')} {declared.get('last_name', '')}".strip()
                d_pct = declared.get("ownership_pct") or declared.get("percentage")
                if not d_name:
                    continue
                best_match = None
                best_sim = 0
                for ext in extracted_entities:
                    e_name = ext.get("name", "") or ext.get("full_name", "")
                    sim = _name_similarity(d_name, e_name) if d_name and e_name else 0
                    if sim > best_sim:
                        best_sim = sim
                        best_match = ext
                if best_match and best_sim >= NAME_MATCH_WARN_THRESHOLD:
                    matched += 1
                    e_pct = best_match.get("percentage") or best_match.get("ownership_pct")
                    if d_pct is not None and e_pct is not None:
                        try:
                            if abs(float(d_pct) - float(e_pct)) > 2.0:
                                mismatches.append(f"{d_name}: declared {d_pct}%, chart {e_pct}%")
                        except (ValueError, TypeError):
                            pass
            if mismatches:
                results.append(_fail(id_, label, cls,
                                     f"Structure chart ownership mismatch: {'; '.join(mismatches)}",
                                     ps_field=PSField.SHAREHOLDERS,
                                     rule_type=rtype))
            else:
                results.append(_pass(id_, label, cls,
                                     f"Structure chart ownership verified for {matched}/{len(declared_shareholders)} declared holders",
                                     ps_field=PSField.SHAREHOLDERS,
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

        # ── Incorporation Date Match (DOC-06A) ──
        elif id_ == "DOC-06A":
            declared_inc_date = ps_get(PSField.INCORPORATION_DATE, "incorporation_date")
            extracted_inc_date = ef.get("incorporation_date") or ef.get("date_of_incorporation")
            if not declared_inc_date:
                results.append(_warn(id_, label, cls,
                                     "Incorporation date not declared in pre-screening",
                                     rule_type=rtype))
                continue
            if not extracted_inc_date:
                results.append(_warn(id_, label, cls,
                                     "Incorporation date could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            d_d = _parse_date(declared_inc_date)
            d_e = _parse_date(extracted_inc_date)
            if not d_d or not d_e:
                # Cannot parse one or both dates — never silently pass
                results.append(_warn(id_, label, cls,
                                     f"Date could not be parsed for comparison "
                                     f"(declared: '{declared_inc_date}' → {d_d}, "
                                     f"extracted: '{extracted_inc_date}' → {d_e}) — manual check required",
                                     ps_field=PSField.INCORPORATION_DATE,
                                     ps_value=str(declared_inc_date),
                                     extracted_value=str(extracted_inc_date),
                                     rule_type=rtype))
            elif d_d == d_e:
                results.append(_pass(id_, label, cls,
                                     f"Incorporation date matches ({extracted_inc_date})",
                                     ps_field=PSField.INCORPORATION_DATE,
                                     ps_value=str(declared_inc_date),
                                     extracted_value=str(extracted_inc_date),
                                     rule_type=rtype))
            else:
                results.append(_fail(id_, label, cls,
                                     f"Incorporation date mismatch: document has '{extracted_inc_date}', "
                                     f"declared is '{declared_inc_date}'",
                                     ps_field=PSField.INCORPORATION_DATE,
                                     ps_value=str(declared_inc_date),
                                     extracted_value=str(extracted_inc_date),
                                     rule_type=rtype))

        # ── Jurisdiction Match (DOC-07) ──
        elif id_ == "DOC-07":
            declared_jur = ps_get(PSField.JURISDICTION, "country_of_incorporation", "country")
            extracted_jur = ef.get("jurisdiction") or ef.get("country") or ef.get("country_of_incorporation")
            if not declared_jur or not extracted_jur:
                results.append(_warn(id_, label, cls,
                                     "Jurisdiction not extractable or not declared — manual check required",
                                     rule_type=rtype))
                continue
            # Canonical country comparison (replaces broken prefix logic)
            if _countries_match(declared_jur, extracted_jur):
                results.append(_pass(id_, label, cls,
                                     f"Jurisdiction matches ({extracted_jur})",
                                     ps_field=PSField.JURISDICTION,
                                     ps_value=declared_jur,
                                     extracted_value=extracted_jur,
                                     rule_type=rtype))
            else:
                results.append(_fail(id_, label, cls,
                                     f"Jurisdiction mismatch: document has '{extracted_jur}', "
                                     f"declared is '{declared_jur}'",
                                     ps_field=PSField.JURISDICTION,
                                     ps_value=declared_jur,
                                     extracted_value=extracted_jur,
                                     rule_type=rtype))

        # ��─ Authorised Share Capital Match (DOC-13) ──
        elif id_ == "DOC-13":
            declared_cap = ps_get(PSField.AUTHORISED_CAPITAL, "authorised_share_capital")
            extracted_cap = ef.get("authorised_share_capital") or ef.get("share_capital")
            if not declared_cap:
                results.append(_warn(id_, label, cls,
                                     "Authorised share capital not declared in pre-screening",
                                     rule_type=rtype))
                continue
            if not extracted_cap:
                results.append(_warn(id_, label, cls,
                                     "Authorised share capital could not be extracted — manual check required",
                                     rule_type=rtype))
                continue
            try:
                d_val = float(re.sub(r"[^\d.]", "", str(declared_cap)))
                e_val = float(re.sub(r"[^\d.]", "", str(extracted_cap)))
                if abs(d_val - e_val) / max(d_val, 1) < 0.001:
                    results.append(_pass(id_, label, cls,
                                         f"Authorised share capital matches ({extracted_cap})",
                                         ps_field=PSField.AUTHORISED_CAPITAL,
                                         ps_value=str(declared_cap),
                                         extracted_value=str(extracted_cap),
                                         rule_type=rtype))
                else:
                    results.append(_fail(id_, label, cls,
                                         f"Capital mismatch: document has '{extracted_cap}', "
                                         f"declared is '{declared_cap}'",
                                         ps_field=PSField.AUTHORISED_CAPITAL,
                                         ps_value=str(declared_cap),
                                         extracted_value=str(extracted_cap),
                                         rule_type=rtype))
            except (ValueError, TypeError):
                results.append(_warn(id_, label, cls,
                                     f"Could not parse capital values for comparison "
                                     f"(declared: '{declared_cap}', extracted: '{extracted_cap}')",
                                     rule_type=rtype))

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
            # DB overrides take priority — only send checks explicitly classified as hybrid or AI.
            # Checks without classification are NOT sent to Claude (safe default).
            ai_hybrid_checks = [c for c in check_overrides
                                 if c.get("classification") in
                                 (CheckClassification.AI, CheckClassification.HYBRID)]
            unclassified = [c for c in check_overrides
                            if not c.get("classification")]
            if unclassified:
                logger.warning(
                    f"[verify-layered] {len(unclassified)} check(s) for {doc_type} have no "
                    f"classification and will NOT be sent to Claude: "
                    f"{[c.get('id', c.get('label', '?')) for c in unclassified]}"
                )
        else:
            ai_hybrid_checks = get_ai_checks_for_doc_type(doc_type, category)

        if ai_hybrid_checks:
            # Build pre-screening context for AI: extract declared values for each check's ps_field
            ps_context = {}
            if prescreening_data and ai_hybrid_checks:
                for chk in ai_hybrid_checks:
                    pf = chk.get("ps_field")
                    if pf:
                        val = prescreening_data.get(pf)
                        if val not in (None, "", [], {}):
                            ps_context[pf] = val
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
                    prescreening_context=ps_context,
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
