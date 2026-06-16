"""
Reference-only country-risk snapshot service.

PR-CR1R restores manual Risk Scoring Model settings as the active pilot source
of truth. This module remains only as dormant scaffolding for future governed
country-risk work; scoring, memo evidence, gates, and UI do not use it.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("arie.country_risk")

ACTIVE_SNAPSHOT_ID = "country-risk-fatf-2026-02-13-v1"
ACTIVE_SNAPSHOT_VERSION = "FATF-2026-02-13+REGMIND-POLICY-V1"
FATF_INCREASED_MONITORING_URL = (
    "https://www.fatf-gafi.org/en/publications/"
    "High-risk-and-other-monitored-jurisdictions/"
    "increased-monitoring-february-2026.html"
)
FATF_CALL_FOR_ACTION_URL = (
    "https://www.fatf-gafi.org/en/publications/"
    "High-risk-and-other-monitored-jurisdictions/"
    "Call-for-action-february-2026.html"
)
INTERNAL_POLICY_REF = "internal://country-risk-policy/regmind-pilot-v1"
SOURCE_PUBLICATION_DATE = "2026-02-13"
EFFECTIVE_DATE = "2026-02-13"
DEFAULT_FRESHNESS_DAYS = 180


_ALIASES = {
    "uk": "united kingdom",
    "gb": "united kingdom",
    "gbr": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "england and wales": "united kingdom",
    "england & wales": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "us": "united states",
    "usa": "united states",
    "united states of america": "united states",
    "uae": "united arab emirates",
    "emirates": "united arab emirates",
    "korea": "south korea",
    "republic of korea": "south korea",
    "hk": "hong kong",
    "sg": "singapore",
    "bvi": "british virgin islands",
    "virgin islands uk": "virgin islands (uk)",
    "uk virgin islands": "virgin islands (uk)",
    "drc": "democratic republic of congo",
    "dr congo": "democratic republic of congo",
    "lao pdr": "laos",
    "cote d ivoire": "cote d'ivoire",
    "côte d'ivoire": "cote d'ivoire",
    "ivory coast": "cote d'ivoire",
    "dprk": "north korea",
    "north korea (dprk)": "north korea",
}

_ISO = {
    "afghanistan": ("AF", "AFG"), "algeria": ("DZ", "DZA"), "angola": ("AO", "AGO"),
    "australia": ("AU", "AUS"), "austria": ("AT", "AUT"), "bahrain": ("BH", "BHR"),
    "belarus": ("BY", "BLR"), "belgium": ("BE", "BEL"), "bermuda": ("BM", "BMU"),
    "bolivia": ("BO", "BOL"), "botswana": ("BW", "BWA"), "brazil": ("BR", "BRA"),
    "british virgin islands": ("VG", "VGB"), "bulgaria": ("BG", "BGR"),
    "burkina faso": ("BF", "BFA"), "cameroon": ("CM", "CMR"), "canada": ("CA", "CAN"),
    "cayman islands": ("KY", "CYM"), "chile": ("CL", "CHL"), "china": ("CN", "CHN"),
    "cote d'ivoire": ("CI", "CIV"), "crimea": ("UA", "UKR"), "cuba": ("CU", "CUB"),
    "democratic republic of congo": ("CD", "COD"), "denmark": ("DK", "DNK"),
    "eritrea": ("ER", "ERI"), "estonia": ("EE", "EST"), "finland": ("FI", "FIN"),
    "france": ("FR", "FRA"), "germany": ("DE", "DEU"), "ghana": ("GH", "GHA"),
    "guernsey": ("GG", "GGY"), "haiti": ("HT", "HTI"), "hong kong": ("HK", "HKG"),
    "iceland": ("IS", "ISL"), "india": ("IN", "IND"), "indonesia": ("ID", "IDN"),
    "iran": ("IR", "IRN"), "iraq": ("IQ", "IRQ"), "ireland": ("IE", "IRL"),
    "isle of man": ("IM", "IMN"), "israel": ("IL", "ISR"), "italy": ("IT", "ITA"),
    "japan": ("JP", "JPN"), "jersey": ("JE", "JEY"), "jordan": ("JO", "JOR"),
    "kenya": ("KE", "KEN"), "kuwait": ("KW", "KWT"), "laos": ("LA", "LAO"),
    "lebanon": ("LB", "LBN"), "libya": ("LY", "LBY"), "liechtenstein": ("LI", "LIE"),
    "luxembourg": ("LU", "LUX"), "malaysia": ("MY", "MYS"), "mali": ("ML", "MLI"),
    "marshall islands": ("MH", "MHL"), "mauritius": ("MU", "MUS"), "mexico": ("MX", "MEX"),
    "monaco": ("MC", "MCO"), "morocco": ("MA", "MAR"), "mozambique": ("MZ", "MOZ"),
    "myanmar": ("MM", "MMR"), "namibia": ("NA", "NAM"), "nepal": ("NP", "NPL"),
    "netherlands": ("NL", "NLD"), "new zealand": ("NZ", "NZL"), "nigeria": ("NG", "NGA"),
    "north korea": ("KP", "PRK"), "norway": ("NO", "NOR"), "oman": ("OM", "OMN"),
    "pakistan": ("PK", "PAK"), "panama": ("PA", "PAN"), "papua new guinea": ("PG", "PNG"),
    "philippines": ("PH", "PHL"), "portugal": ("PT", "PRT"), "qatar": ("QA", "QAT"),
    "russia": ("RU", "RUS"), "rwanda": ("RW", "RWA"), "samoa": ("WS", "WSM"),
    "saudi arabia": ("SA", "SAU"), "senegal": ("SN", "SEN"), "seychelles": ("SC", "SYC"),
    "singapore": ("SG", "SGP"), "somalia": ("SO", "SOM"), "south africa": ("ZA", "ZAF"),
    "south korea": ("KR", "KOR"), "south sudan": ("SS", "SSD"), "spain": ("ES", "ESP"),
    "sri lanka": ("LK", "LKA"), "sudan": ("SD", "SDN"), "sweden": ("SE", "SWE"),
    "switzerland": ("CH", "CHE"), "syria": ("SY", "SYR"), "taiwan": ("TW", "TWN"),
    "tanzania": ("TZ", "TZA"), "tunisia": ("TN", "TUN"), "turkey": ("TR", "TUR"),
    "uganda": ("UG", "UGA"), "united arab emirates": ("AE", "ARE"),
    "united kingdom": ("GB", "GBR"), "united states": ("US", "USA"),
    "vanuatu": ("VU", "VUT"), "venezuela": ("VE", "VEN"), "vietnam": ("VN", "VNM"),
    "virgin islands (uk)": ("VG", "VGB"), "yemen": ("YE", "YEM"),
}


def normalize_country_key(country):
    value = str(country or "").strip().lower()
    if not value:
        return ""
    value = " ".join(value.replace(",", " ").split())
    for prefix in ("republic of ", "state of ", "the ", "federation of "):
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix):].strip()
    return _ALIASES.get(value, value)


def risk_score_to_rating(score):
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 2
    return {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "VERY_HIGH"}.get(score, "MEDIUM")


def _entry(country, score, *, fatf_status="none", sanctions_status="none",
           high_risk_status="none", source_name=None, source_url=None, notes=""):
    key = normalize_country_key(country)
    iso2, iso3 = _ISO.get(key, ("", ""))
    if source_name is None:
        source_name = "RegMind internal country-risk policy v1"
    if source_url is None:
        source_url = INTERNAL_POLICY_REF
    source_publication_date = SOURCE_PUBLICATION_DATE if fatf_status != "none" else "2026-02-13"
    if fatf_status == "call_for_action":
        source_name = "FATF High-Risk Jurisdictions subject to a Call for Action"
        source_url = FATF_CALL_FOR_ACTION_URL
    elif fatf_status == "increased_monitoring":
        source_name = "FATF Jurisdictions under Increased Monitoring"
        source_url = FATF_INCREASED_MONITORING_URL
    payload = {
        "snapshot_id": ACTIVE_SNAPSHOT_ID,
        "country_name": country,
        "country_key": key,
        "iso_alpha2": iso2,
        "iso_alpha3": iso3,
        "risk_rating": risk_score_to_rating(score),
        "risk_score": int(score),
        "fatf_status": fatf_status,
        "sanctions_status": sanctions_status,
        "high_risk_status": high_risk_status,
        "source_name": source_name,
        "source_url": source_url,
        "source_publication_date": source_publication_date,
        "effective_date": EFFECTIVE_DATE,
        "status": "active",
        "notes": notes,
        "previous_risk_rating": "",
        "previous_fatf_status": "",
    }
    payload["checksum"] = _checksum(payload)
    payload["id"] = f"{ACTIVE_SNAPSHOT_ID}:{key}"
    return payload


def _checksum(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entries():
    rows = []
    for country in (
        "australia", "canada", "france", "germany", "hong kong", "ireland",
        "japan", "luxembourg", "netherlands", "new zealand", "singapore",
        "switzerland", "united kingdom", "united states", "austria", "belgium",
        "denmark", "finland", "norway", "sweden", "south korea", "israel",
        "iceland", "italy", "portugal", "spain", "taiwan",
    ):
        rows.append(_entry(country, 1, notes="Low-risk jurisdiction under internal pilot policy."))
    for country in (
        "bahrain", "botswana", "brazil", "chile", "china", "india", "indonesia",
        "malaysia", "mauritius", "mexico", "morocco", "oman", "qatar", "rwanda",
        "saudi arabia", "turkey", "united arab emirates", "uganda", "ghana",
        "jordan", "sri lanka", "tunisia", "jersey", "guernsey", "isle of man",
        "liechtenstein", "estonia", "pakistan", "seychelles",
    ):
        rows.append(_entry(country, 2, notes="Medium jurisdiction risk under internal pilot policy."))
    for country in (
        "algeria", "angola", "bolivia", "bulgaria", "cameroon", "cote d'ivoire",
        "democratic republic of congo", "haiti", "kenya", "kuwait", "laos",
        "lebanon", "monaco", "namibia", "nepal", "papua new guinea",
        "south sudan", "venezuela", "vietnam", "yemen",
    ):
        rows.append(_entry(country, 3, fatf_status="increased_monitoring"))
    rows.append(_entry("syria", 4, fatf_status="increased_monitoring",
                       sanctions_status="sanctioned",
                       notes="FATF increased monitoring plus sanctions/internal policy floor."))
    for country in ("iran", "north korea", "myanmar"):
        rows.append(_entry(country, 4, fatf_status="call_for_action",
                           sanctions_status="sanctioned" if country in {"iran", "north korea", "myanmar"} else "none"))
    for country in (
        "burkina faso", "mali", "mozambique", "nigeria", "philippines", "senegal",
        "south africa", "tanzania", "iraq", "bermuda", "vanuatu", "samoa",
        "marshall islands",
    ):
        rows.append(_entry(country, 3, high_risk_status="internal_high_risk",
                           notes="Internal high-risk policy classification; not represented as current FATF status."))
    for country in ("russia", "belarus", "cuba", "crimea"):
        rows.append(_entry(country, 4, sanctions_status="sanctioned"))
    for country in ("afghanistan", "somalia", "libya", "eritrea", "sudan"):
        rows.append(_entry(country, 4, high_risk_status="internal_very_high"))
    for country in ("british virgin islands", "virgin islands (uk)"):
        rows.append(_entry(country, 4, fatf_status="increased_monitoring",
                           high_risk_status="secrecy_jurisdiction"))
    for country in ("cayman islands", "panama"):
        rows.append(_entry(country, 4, high_risk_status="secrecy_jurisdiction"))
    deduped = {}
    for row in rows:
        deduped[row["country_key"]] = row
    return list(deduped.values())


DEFAULT_COUNTRY_RISK_ENTRIES = _entries()
DEFAULT_COUNTRY_RISK_SNAPSHOT = {
    "id": ACTIVE_SNAPSHOT_ID,
    "version": ACTIVE_SNAPSHOT_VERSION,
    "status": "superseded",
    "source_name": "FATF February 2026 public statements + RegMind internal country-risk policy v1",
    "source_url": FATF_INCREASED_MONITORING_URL,
    "source_publication_date": SOURCE_PUBLICATION_DATE,
    "effective_date": EFFECTIVE_DATE,
    "imported_by": "system",
    "checksum": _checksum({"entries": DEFAULT_COUNTRY_RISK_ENTRIES, "version": ACTIVE_SNAPSHOT_VERSION}),
    "freshness_days": DEFAULT_FRESHNESS_DAYS,
    "notes": (
        "Reference-only PR-CR1 seed snapshot. FATF statuses are sourced from FATF public "
        "statements dated 2026-02-13; non-FATF ratings are RegMind internal "
        "pilot policy classifications. PR-CR1R disables this snapshot as an "
        "operational scoring/memo/gate source."
    ),
}


def seed_default_country_risk_snapshot(db):
    existing = db.execute(
        "SELECT id FROM country_risk_snapshots WHERE id=? LIMIT 1",
        (ACTIVE_SNAPSHOT_ID,),
    ).fetchone()
    if existing:
        return False
    snapshot = DEFAULT_COUNTRY_RISK_SNAPSHOT
    db.execute(
        """
        INSERT OR IGNORE INTO country_risk_snapshots
            (id, version, status, source_name, source_url, source_publication_date,
             effective_date, imported_by, checksum, freshness_days, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["id"], snapshot["version"], snapshot["status"],
            snapshot["source_name"], snapshot["source_url"],
            snapshot["source_publication_date"], snapshot["effective_date"],
            snapshot["imported_by"], snapshot["checksum"],
            snapshot["freshness_days"], snapshot["notes"],
        ),
    )
    for row in DEFAULT_COUNTRY_RISK_ENTRIES:
        db.execute(
            """
            INSERT OR IGNORE INTO country_risk_entries
                (id, snapshot_id, country_name, country_key, iso_alpha2, iso_alpha3,
                 risk_rating, risk_score, fatf_status, sanctions_status,
                 high_risk_status, source_name, source_url, source_publication_date,
                 effective_date, status, checksum, notes, previous_risk_rating,
                 previous_fatf_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["snapshot_id"], row["country_name"], row["country_key"],
                row["iso_alpha2"], row["iso_alpha3"], row["risk_rating"],
                row["risk_score"], row["fatf_status"], row["sanctions_status"],
                row["high_risk_status"], row["source_name"], row["source_url"],
                row["source_publication_date"], row["effective_date"], row["status"],
                row["checksum"], row["notes"], row["previous_risk_rating"],
                row["previous_fatf_status"],
            ),
        )
    return True


def _parse_ts(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _is_stale(snapshot):
    imported = _parse_ts((snapshot or {}).get("last_checked_at") or (snapshot or {}).get("imported_at"))
    if not imported:
        return True
    try:
        freshness_days = int((snapshot or {}).get("freshness_days") or DEFAULT_FRESHNESS_DAYS)
    except (TypeError, ValueError):
        freshness_days = DEFAULT_FRESHNESS_DAYS
    age_days = (datetime.now(timezone.utc) - imported).days
    return age_days > freshness_days


def active_country_risk_snapshot(db=None):
    close_db = False
    if db is None:
        from db import get_db
        db = get_db()
        close_db = True
    try:
        snapshot = db.execute(
            "SELECT * FROM country_risk_snapshots WHERE status='active' ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
        if snapshot:
            snapshot["is_stale"] = _is_stale(snapshot)
        return snapshot
    finally:
        if close_db:
            db.close()


def lookup_country_risk(country, db=None):
    key = normalize_country_key(country)
    if not key:
        return _unknown_country_result(country, key, "missing_country")
    close_db = False
    if db is None:
        from db import get_db
        db = get_db()
        close_db = True
    try:
        snapshot = active_country_risk_snapshot(db)
        if not snapshot:
            return _unknown_country_result(country, key, "no_active_snapshot")
        row = db.execute(
            """
            SELECT * FROM country_risk_entries
            WHERE snapshot_id=? AND status='active' AND country_key=?
            LIMIT 1
            """,
            (snapshot["id"], key),
        ).fetchone()
        if not row:
            return _unknown_country_result(country, key, "country_not_in_active_snapshot", snapshot)
        row["found"] = True
        row["is_unknown"] = False
        row["defaulted"] = False
        row["snapshot_id"] = snapshot["id"]
        row["snapshot_version"] = snapshot["version"]
        row["snapshot_checksum"] = snapshot["checksum"]
        row["snapshot_source_name"] = snapshot["source_name"]
        row["last_checked_at"] = snapshot.get("last_checked_at") or snapshot.get("imported_at")
        row["snapshot_imported_at"] = snapshot.get("imported_at")
        row["is_stale"] = bool(snapshot.get("is_stale"))
        row["stale_warning"] = (
            "Country-risk source freshness has expired."
            if row["is_stale"] else ""
        )
        return row
    except Exception as exc:
        logger.warning("country_risk_lookup_failed country=%s error=%s", country, exc)
        return _unknown_country_result(country, key, "lookup_error")
    finally:
        if close_db:
            db.close()


def _unknown_country_result(country, key, reason, snapshot=None):
    return {
        "found": False,
        "is_unknown": True,
        "defaulted": True,
        "lookup_reason": reason,
        "country_name": country or "",
        "country_key": key,
        "iso_alpha2": "",
        "iso_alpha3": "",
        "risk_rating": "MEDIUM",
        "risk_score": 2,
        "fatf_status": "none",
        "sanctions_status": "none",
        "high_risk_status": "unknown_country",
        "source_name": "RegMind default unknown-country policy",
        "source_url": "internal://country-risk-policy/default-unknown",
        "source_publication_date": "",
        "effective_date": "",
        "snapshot_id": (snapshot or {}).get("id", ""),
        "snapshot_version": (snapshot or {}).get("version", ""),
        "snapshot_checksum": (snapshot or {}).get("checksum", ""),
        "last_checked_at": (snapshot or {}).get("last_checked_at") or (snapshot or {}).get("imported_at", ""),
        "is_stale": bool((snapshot or {}).get("is_stale", False)),
        "stale_warning": (
            "Unknown country defaulted to MEDIUM. Compliance review should confirm jurisdiction risk."
        ),
        "notes": "Unknown countries fail safe to MEDIUM, never LOW.",
    }


def list_country_risk_entries(db=None):
    close_db = False
    if db is None:
        from db import get_db
        db = get_db()
        close_db = True
    try:
        snapshot = active_country_risk_snapshot(db)
        if not snapshot:
            return {"snapshot": None, "entries": []}
        rows = db.execute(
            """
            SELECT * FROM country_risk_entries
            WHERE snapshot_id=? AND status='active'
            ORDER BY risk_score DESC, country_name ASC
            """,
            (snapshot["id"],),
        ).fetchall()
        for row in rows:
            row["snapshot_version"] = snapshot["version"]
            row["is_stale"] = bool(snapshot.get("is_stale"))
            row["last_checked_at"] = snapshot.get("last_checked_at") or snapshot.get("imported_at")
        return {"snapshot": snapshot, "entries": rows}
    finally:
        if close_db:
            db.close()
