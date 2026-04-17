"""
Screening Normalizer / Denormalizer — SCR-006
==============================================
Converts raw ``screening_report`` dicts (as returned by
``screening.run_full_screening()``) to/from the normalized model.

Critical invariant:
    ``denormalize_to_legacy(normalize_screening_report(raw)) == raw``

The normalizer **adds** metadata fields; the denormalizer **strips** them.
Nested ``screening.results[]`` arrays are passed through untouched.
"""

import copy
import logging

from screening_models import (
    NORMALIZER_ADDED_REPORT_KEYS,
    NORMALIZER_ADDED_PERSON_KEYS,
    _summarise_person_screening,
)

logger = logging.getLogger("arie")

# Current schema version
_NORMALIZED_VERSION = "1.0"


def normalize_screening_report(raw_report: dict,
                                provider: str = "sumsub") -> dict:
    """
    Add normalizer metadata to a raw screening report.

    The original dict is **not** mutated — a deep copy is used.

    Added keys (report level):
        provider, normalized_version, any_pep_hits,
        any_sanctions_hits, total_persons_screened

    Added keys (per-person screening):
        has_pep_hit, has_sanctions_hit, has_adverse_media_hit
    """
    if not isinstance(raw_report, dict):
        raise ValueError("raw_report must be a dict")

    out = copy.deepcopy(raw_report)

    # ── Normalize person-level screenings ──
    for key in ("director_screenings", "ubo_screenings"):
        persons = out.get(key, []) or []
        for person in persons:
            if not isinstance(person, dict):
                continue
            screening = person.get("screening", {}) or {}
            summary = _summarise_person_screening(screening)
            person["has_pep_hit"] = summary["has_pep_hit"]
            person["has_sanctions_hit"] = summary["has_sanctions_hit"]
            person["has_adverse_media_hit"] = summary["has_adverse_media_hit"]

    # ── Compute report-level summaries ──
    all_persons = (out.get("director_screenings", []) or []) + \
                  (out.get("ubo_screenings", []) or [])

    any_pep = any(
        isinstance(p, dict) and p.get("has_pep_hit")
        for p in all_persons
    )
    any_sanctions_person = any(
        isinstance(p, dict) and p.get("has_sanctions_hit")
        for p in all_persons
    )
    # Company sanctions
    company_screening = out.get("company_screening", {}) or {}
    company_sanctions = company_screening.get("sanctions", {}) or {}
    any_sanctions = any_sanctions_person or bool(company_sanctions.get("matched"))

    out["provider"] = provider
    out["normalized_version"] = _NORMALIZED_VERSION
    out["any_pep_hits"] = any_pep
    out["any_sanctions_hits"] = any_sanctions
    out["total_persons_screened"] = len(
        [p for p in all_persons if isinstance(p, dict)]
    )

    return out


def denormalize_to_legacy(normalized: dict) -> dict:
    """
    Strip normalizer-added metadata and return the legacy dict shape.

    This must produce output identical to the original raw report
    that was fed into ``normalize_screening_report()``.
    """
    if not isinstance(normalized, dict):
        raise ValueError("normalized must be a dict")

    out = copy.deepcopy(normalized)

    # ── Strip report-level metadata ──
    for key in NORMALIZER_ADDED_REPORT_KEYS:
        out.pop(key, None)

    # ── Strip person-level metadata ──
    for list_key in ("director_screenings", "ubo_screenings"):
        persons = out.get(list_key, []) or []
        for person in persons:
            if not isinstance(person, dict):
                continue
            for key in NORMALIZER_ADDED_PERSON_KEYS:
                person.pop(key, None)

    return out
