"""
Normalized Screening Schema — Minimal Dict-Based Model
=======================================================
Defines the normalized screening report shape that sits alongside the
legacy ``screening_report`` in ``prescreening_data``.

Design principles:
- Plain dicts, not dataclasses — matches codebase style.
- Nested ``screening.results[]`` arrays are **pass-through**; the normalizer
  never restructures them.
- Added metadata (``provider``, ``normalized_version``, per-person summaries)
  is stripped by the denormalizer so round-trip parity holds.
"""

# ---------------------------------------------------------------------------
# Schema documentation (reference only — not enforced at import time)
# ---------------------------------------------------------------------------
NORMALIZED_PERSON_SCREENING_SCHEMA = {
    "person_name":       "str — full name",
    "person_type":       "str — 'director' | 'ubo'",
    "nationality":       "str — ISO country or freetext",
    "declared_pep":      "str — 'Yes' | 'No'",
    "screening":         "dict — original screening result dict (pass-through)",
    # Added by normalizer:
    "has_pep_hit":       "bool — any result with is_pep == True",
    "has_sanctions_hit": "bool — any result with is_sanctioned == True",
    "has_adverse_media_hit": "bool — reserved, currently always False",
    # Optional legacy fields (pass-through when present):
    "undeclared_pep":    "bool — set by screening when PEP not declared",
    "ownership_pct":     "number — UBO ownership percentage (UBOs only)",
}

NORMALIZED_COMPANY_SCREENING_SCHEMA = {
    "company_screening": "dict — original company screening result (pass-through)",
    # Added by normalizer:
    "has_sanctions_hit": "bool — company sanctions match found",
}

NORMALIZED_SCREENING_REPORT_SCHEMA = {
    "screened_at":         "str — ISO-8601 timestamp",
    "company_screening":   "dict — original company screening",
    "director_screenings": "list[dict] — normalized person screenings for directors",
    "ubo_screenings":      "list[dict] — normalized person screenings for UBOs",
    "ip_geolocation":      "dict — original IP geolocation result (pass-through)",
    "overall_flags":       "list[str] — screening flags",
    "total_hits":          "int — total hit count",
    "degraded_sources":    "list[str] — sources that returned errors/degraded",
    "kyc_applicants":      "list[dict] — KYC applicant results (pass-through)",
    "screening_mode":      "str — 'live' | 'simulated' | 'unknown'",
    # Added by normalizer:
    "provider":            "str — screening provider name, e.g. 'sumsub'",
    "normalized_version":  "str — schema version, e.g. '1.0'",
    "any_pep_hits":        "bool — True if any person screening has PEP hit",
    "any_sanctions_hits":  "bool — True if any person or company has sanctions hit",
    "total_persons_screened": "int — directors + UBOs screened",
}

# Keys added by the normalizer that must be stripped during denormalization
NORMALIZER_ADDED_REPORT_KEYS = frozenset({
    "provider",
    "normalized_version",
    "any_pep_hits",
    "any_sanctions_hits",
    "total_persons_screened",
})

NORMALIZER_ADDED_PERSON_KEYS = frozenset({
    "has_pep_hit",
    "has_sanctions_hit",
    "has_adverse_media_hit",
})


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def _summarise_person_screening(screening: dict) -> dict:
    """Derive per-person summary flags from a screening result dict."""
    results = []
    if isinstance(screening, dict):
        results = screening.get("results", []) or []
    has_pep = any(isinstance(r, dict) and r.get("is_pep") for r in results)
    has_sanctions = any(isinstance(r, dict) and r.get("is_sanctioned") for r in results)
    return {
        "has_pep_hit": has_pep,
        "has_sanctions_hit": has_sanctions,
        "has_adverse_media_hit": False,  # reserved — no provider yet
    }


def create_normalized_person_screening(**kwargs) -> dict:
    """
    Create a normalized person screening dict.

    Required kwargs: person_name, person_type, screening
    Optional: nationality, declared_pep, undeclared_pep, ownership_pct
    """
    screening = kwargs.get("screening", {}) or {}
    summary = _summarise_person_screening(screening)
    result = {
        "person_name":  kwargs.get("person_name", ""),
        "person_type":  kwargs.get("person_type", ""),
        "nationality":  kwargs.get("nationality", ""),
        "declared_pep": kwargs.get("declared_pep", "No"),
        "screening":    screening,
        "has_pep_hit":           summary["has_pep_hit"],
        "has_sanctions_hit":     summary["has_sanctions_hit"],
        "has_adverse_media_hit": summary["has_adverse_media_hit"],
    }
    # Pass through optional legacy keys when present
    if "undeclared_pep" in kwargs:
        result["undeclared_pep"] = kwargs["undeclared_pep"]
    if "ownership_pct" in kwargs:
        result["ownership_pct"] = kwargs["ownership_pct"]
    return result


def create_normalized_company_screening(**kwargs) -> dict:
    """
    Create a normalized company screening dict.

    Required kwargs: company_screening
    """
    cs = kwargs.get("company_screening", {}) or {}
    sanctions = cs.get("sanctions", {}) or {}
    has_sanctions = bool(sanctions.get("matched"))
    return {
        "company_screening": cs,
        "has_sanctions_hit":  has_sanctions,
    }


def create_normalized_screening_report(**kwargs) -> dict:
    """
    Create a normalized screening report dict.

    Required kwargs: screened_at, director_screenings, ubo_screenings
    Optional: company_screening, ip_geolocation, overall_flags, total_hits,
              degraded_sources, kyc_applicants, screening_mode, provider
    """
    directors = kwargs.get("director_screenings", []) or []
    if not isinstance(directors, list):
        directors = []
    ubos = kwargs.get("ubo_screenings", []) or []
    if not isinstance(ubos, list):
        ubos = []
    company = kwargs.get("company_screening", {}) or {}
    company_sanctions = (company.get("sanctions") or {})

    any_pep = any(d.get("has_pep_hit") for d in directors + ubos)
    any_sanctions = (
        any(d.get("has_sanctions_hit") for d in directors + ubos)
        or bool(company_sanctions.get("matched"))
    )

    return {
        "screened_at":        kwargs.get("screened_at", ""),
        "company_screening":  company,
        "director_screenings": directors,
        "ubo_screenings":     ubos,
        "ip_geolocation":     kwargs.get("ip_geolocation", {}),
        "overall_flags":      kwargs.get("overall_flags", []),
        "total_hits":         kwargs.get("total_hits", 0),
        "degraded_sources":   kwargs.get("degraded_sources", []),
        "kyc_applicants":     kwargs.get("kyc_applicants", []),
        "screening_mode":     kwargs.get("screening_mode", ""),
        # Normalizer metadata
        "provider":                kwargs.get("provider", "sumsub"),
        "normalized_version":      kwargs.get("normalized_version", "1.0"),
        "any_pep_hits":            any_pep,
        "any_sanctions_hits":      any_sanctions,
        "total_persons_screened":  len(directors) + len(ubos),
    }


def validate_normalized_report(report: dict) -> list:
    """
    Validate a normalized screening report dict.

    Returns a list of error strings.  Empty list = valid.
    """
    errors = []
    if not isinstance(report, dict):
        return ["report must be a dict"]

    required_keys = [
        "screened_at", "director_screenings", "ubo_screenings",
        "provider", "normalized_version",
    ]
    for key in required_keys:
        if key not in report:
            errors.append(f"missing required key: {key}")

    if "director_screenings" in report:
        ds = report["director_screenings"]
        if not isinstance(ds, list):
            errors.append("director_screenings must be a list")
        else:
            for i, d in enumerate(ds):
                if not isinstance(d, dict):
                    errors.append(f"director_screenings[{i}] must be a dict")
                elif "person_name" not in d:
                    errors.append(f"director_screenings[{i}] missing person_name")

    if "ubo_screenings" in report:
        us = report["ubo_screenings"]
        if not isinstance(us, list):
            errors.append("ubo_screenings must be a list")
        else:
            for i, u in enumerate(us):
                if not isinstance(u, dict):
                    errors.append(f"ubo_screenings[{i}] must be a dict")
                elif "person_name" not in u:
                    errors.append(f"ubo_screenings[{i}] missing person_name")

    if "provider" in report and not isinstance(report["provider"], str):
        errors.append("provider must be a string")

    if "normalized_version" in report and not isinstance(report["normalized_version"], str):
        errors.append("normalized_version must be a string")

    return errors
