"""
Screening Normalizer / Denormalizer
====================================
Converts between legacy Sumsub screening report format and the
normalized format defined in screening_models.py.

STRICT RULES:
- No float arithmetic
- No datetime parsing/reformatting
- No JSON round-trip inside functions
- No list reordering
- Preserve screening.results[] pass-through
- Add metadata only
- Do not restructure nested legacy payloads
- Denormalization strips metadata and reconstructs exact legacy dict
- Preserve object references where safe; copy only when mutation is required

INVARIANT:
    denormalize_to_legacy(normalize_screening_report(raw)) == raw
"""

import logging

from screening_state import (
    derive_screening_state,
    derive_subject_state,
    COMPLETED_CLEAR as _SCR_COMPLETED_CLEAR,
    COMPLETED_MATCH as _SCR_COMPLETED_MATCH,
    TERMINAL_STATES as _SCR_TERMINAL_STATES,
)

logger = logging.getLogger("arie.screening_normalizer")

# Metadata keys added during normalization (stripped during denormalization)
_REPORT_METADATA_KEYS = frozenset({
    "provider",
    "normalized_version",
    "any_pep_hits",
    "any_sanctions_hits",
    "total_persons_screened",
    "adverse_media_coverage",
    "company_screening_coverage",
    "has_company_screening_hit",
    # Priority A: canonical state metadata
    "company_screening_state",
    "any_non_terminal_subject",
})

_PERSON_METADATA_KEYS = frozenset({
    "has_pep_hit",
    "has_sanctions_hit",
    "has_adverse_media_hit",
    "adverse_media_coverage",
    # Priority A: canonical state metadata
    "screening_state",
    "requires_review",
})


class AlreadyNormalizedError(Exception):
    """Raised when attempting to normalize an already-normalized report."""
    pass


def _compute_person_hits(person: dict) -> dict:
    """
    Compute per-person hit summary fields from screening results.
    Does not modify the input dict.

    Priority A (truthful semantics): when the underlying screening is **not**
    terminal (pending / not_configured / unavailable / failed / not_started),
    has_pep_hit and has_sanctions_hit are set to ``None`` rather than
    ``False``. ``False`` would mean "we have a real provider answer and
    there is no hit" — which is a misrepresentation when the provider has
    not produced a terminal answer yet.
    """
    screening = person.get("screening", {})
    state = derive_screening_state(screening)
    results = screening.get("results", [])

    if state in _SCR_TERMINAL_STATES:
        # Real provider answer: hits can be asserted truthfully.
        if state == _SCR_COMPLETED_MATCH and results:
            has_pep_hit = any(r.get("is_pep") for r in results)
            has_sanctions_hit = any(r.get("is_sanctioned") for r in results)
        else:
            # COMPLETED_CLEAR (or COMPLETED_MATCH with empty results) → no hit
            has_pep_hit = False
            has_sanctions_hit = False
    else:
        # Non-terminal: cannot assert absence of hits. Stay null.
        has_pep_hit = None
        has_sanctions_hit = None

    return {
        "has_pep_hit": has_pep_hit,
        "has_sanctions_hit": has_sanctions_hit,
        # Sumsub does not provide adverse media screening
        "has_adverse_media_hit": None,
        "adverse_media_coverage": "none",
        # Priority A: canonical state, persisted alongside legacy fields.
        "screening_state": state,
    }


def normalize_screening_report(raw_report: dict) -> dict:
    """
    Normalize a raw Sumsub screening report by adding metadata.

    Does NOT restructure the report — only adds normalization metadata.

    Args:
        raw_report: Raw screening report from run_full_screening().

    Returns:
        dict: The same report with added metadata fields.

    Raises:
        AlreadyNormalizedError: If the report already contains normalized_version.
    """
    if not isinstance(raw_report, dict):
        raise TypeError(f"Expected dict, got {type(raw_report).__name__}")

    if "normalized_version" in raw_report:
        raise AlreadyNormalizedError(
            "Report already contains 'normalized_version'. "
            "Cannot normalize twice."
        )

    # Build the normalized report by copying the raw and adding metadata
    # We need a shallow copy to avoid mutating the original
    normalized = dict(raw_report)

    # Report-level metadata
    normalized["provider"] = "sumsub"
    normalized["normalized_version"] = "1.0"

    # Person-level metadata for directors
    any_pep = False
    any_sanctions = False
    total_persons = 0
    any_non_terminal_subject = False

    new_directors = []
    for d in raw_report.get("director_screenings", []):
        nd = dict(d)  # shallow copy to add metadata
        hits = _compute_person_hits(d)
        nd.update(hits)
        # Priority A: declared_pep stays a separate signal regardless of
        # provider state. requires_review is True for any non-terminal
        # state, terminal match, or declared PEP.
        declared_pep_flag = (d.get("declared_pep") or "").lower() == "yes"
        envelope = derive_subject_state(d.get("screening", {}), declared_pep=declared_pep_flag)
        nd["requires_review"] = envelope["requires_review"]
        if hits["screening_state"] not in _SCR_TERMINAL_STATES:
            any_non_terminal_subject = True
        new_directors.append(nd)
        total_persons += 1
        if hits["has_pep_hit"]:
            any_pep = True
        if hits["has_sanctions_hit"]:
            any_sanctions = True

    new_ubos = []
    for u in raw_report.get("ubo_screenings", []):
        nu = dict(u)  # shallow copy to add metadata
        hits = _compute_person_hits(u)
        nu.update(hits)
        declared_pep_flag = (u.get("declared_pep") or "").lower() == "yes"
        envelope = derive_subject_state(u.get("screening", {}), declared_pep=declared_pep_flag)
        nu["requires_review"] = envelope["requires_review"]
        if hits["screening_state"] not in _SCR_TERMINAL_STATES:
            any_non_terminal_subject = True
        new_ubos.append(nu)
        total_persons += 1
        if hits["has_pep_hit"]:
            any_pep = True
        if hits["has_sanctions_hit"]:
            any_sanctions = True

    normalized["director_screenings"] = new_directors
    normalized["ubo_screenings"] = new_ubos

    # Report-level summaries
    normalized["any_pep_hits"] = any_pep
    normalized["any_sanctions_hits"] = any_sanctions
    normalized["total_persons_screened"] = total_persons
    normalized["any_non_terminal_subject"] = any_non_terminal_subject

    # Adverse media coverage (Sumsub does not provide adverse media)
    normalized["adverse_media_coverage"] = "none"

    # Company screening coverage + canonical state.
    company = raw_report.get("company_screening", {})
    if company:
        normalized["company_screening_coverage"] = "partial"
        sanctions = company.get("sanctions", {})
        company_state = derive_screening_state(sanctions)
        normalized["company_screening_state"] = company_state
        # Priority A: only a terminal state may yield a non-null
        # has_company_screening_hit.
        if company_state == _SCR_COMPLETED_MATCH:
            normalized["has_company_screening_hit"] = True
        elif company_state == _SCR_COMPLETED_CLEAR:
            normalized["has_company_screening_hit"] = False
        else:
            # pending / not_configured / failed → cannot assert presence
            # or absence of a hit.
            normalized["has_company_screening_hit"] = None
            if company_state not in _SCR_TERMINAL_STATES:
                any_non_terminal_subject = True
                normalized["any_non_terminal_subject"] = True
    else:
        normalized["company_screening_coverage"] = "none"
        normalized["has_company_screening_hit"] = None
        normalized["company_screening_state"] = "not_started"

    return normalized


def denormalize_to_legacy(normalized: dict) -> dict:
    """
    Strip normalization metadata to reconstruct the exact legacy dict.

    INVARIANT: denormalize_to_legacy(normalize_screening_report(raw)) == raw

    Args:
        normalized: A normalized screening report.

    Returns:
        dict: The original legacy report structure.
    """
    if not isinstance(normalized, dict):
        raise TypeError(f"Expected dict, got {type(normalized).__name__}")

    # Build legacy by copying and removing metadata keys
    legacy = {}
    for key, value in normalized.items():
        if key in _REPORT_METADATA_KEYS:
            continue
        legacy[key] = value

    # Strip person-level metadata from director_screenings
    if "director_screenings" in legacy:
        legacy_directors = []
        for d in legacy["director_screenings"]:
            ld = {}
            for k, v in d.items():
                if k in _PERSON_METADATA_KEYS:
                    continue
                ld[k] = v
            legacy_directors.append(ld)
        legacy["director_screenings"] = legacy_directors

    # Strip person-level metadata from ubo_screenings
    if "ubo_screenings" in legacy:
        legacy_ubos = []
        for u in legacy["ubo_screenings"]:
            lu = {}
            for k, v in u.items():
                if k in _PERSON_METADATA_KEYS:
                    continue
                lu[k] = v
            legacy_ubos.append(lu)
        legacy["ubo_screenings"] = legacy_ubos

    return legacy
