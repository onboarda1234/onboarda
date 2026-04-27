"""
Normalized Screening Schema — Plain Dict Schemas, Factories, and Validator
==========================================================================
Defines the canonical normalized screening report format for the
ComplyAdvantage migration scaffolding.

SAFETY: No imports from screening.py or sumsub_client.py.
SAFETY: Uses plain dicts, not dataclasses.
"""

from pydantic import BaseModel


class TwoPassProvenance(BaseModel):
    """Top-level metadata for two-pass screening (CA only). Sumsub never sets this."""

    strict_workflow_id: str | None = None
    relaxed_workflow_id: str | None = None
    strict_match_count: int = 0
    relaxed_match_count: int = 0
    merged_match_count: int = 0
    strict_only_count: int = 0
    relaxed_only_count: int = 0
    both_count: int = 0


# ── Coverage Semantics ──
# "none"    = not screened / not available from provider
# "partial" = some checks performed, but not complete
# "full"    = complete screening performed

VALID_COVERAGE_VALUES = ("none", "partial", "full")
VALID_NORMALIZATION_STATUSES = ("success", "failed")

# ── Person Screening Schema ──

NORMALIZED_PERSON_SCREENING_SCHEMA = {
    "person_name": str,
    "person_type": str,         # "director" | "ubo"
    "nationality": str,
    "declared_pep": str,        # "Yes" | "No"
    "has_pep_hit": (bool, type(None)),
    "has_sanctions_hit": (bool, type(None)),
    "has_adverse_media_hit": (bool, type(None)),
    "adverse_media_coverage": str,  # "none" | "partial" | "full"
    "screening": dict,          # Raw screening sub-object (pass-through)
    "screening_state": str,     # values like "completed_clear" / "completed_pep" / "pending"
    "requires_review": bool,
    "is_rca": (bool, type(None)),
    "pep_classes": (list, type(None)),
}

# ── Company Screening Schema ──

NORMALIZED_COMPANY_SCREENING_SCHEMA = {
    "company_screening_coverage": str,  # "none" | "partial" | "full"
    "has_company_screening_hit": (bool, type(None)),
    "company_screening": dict,  # Raw company screening sub-object (pass-through)
}

# ── Full Report Schema ──

NORMALIZED_SCREENING_REPORT_SCHEMA = {
    "provider": str,
    "normalized_version": str,
    "screened_at": str,
    "any_pep_hits": bool,
    "any_sanctions_hits": bool,
    "total_persons_screened": int,
    "adverse_media_coverage": str,
    "company_screening_coverage": str,
    "has_company_screening_hit": (bool, type(None)),
    "company_screening": dict,
    "director_screenings": list,
    "ubo_screenings": list,
    "overall_flags": list,
    "total_hits": int,
    "degraded_sources": list,
    "any_non_terminal_subject": bool,
    "company_screening_state": str,
    "provenance": (dict, type(None)),
}


def create_normalized_person_screening(**kwargs) -> dict:
    """
    Create a normalized person screening dict with safe defaults.
    """
    result = {
        "person_name": kwargs.get("person_name", ""),
        "person_type": kwargs.get("person_type", "director"),
        "nationality": kwargs.get("nationality", ""),
        "declared_pep": kwargs.get("declared_pep", "No"),
        "has_pep_hit": kwargs.get("has_pep_hit", None),
        "has_sanctions_hit": kwargs.get("has_sanctions_hit", None),
        "has_adverse_media_hit": kwargs.get("has_adverse_media_hit", None),
        "adverse_media_coverage": kwargs.get("adverse_media_coverage", "none"),
        "screening": kwargs.get("screening", {}),
    }
    # Pass through any extra keys from the original
    for key in kwargs:
        if key not in result:
            result[key] = kwargs[key]
    return result


def create_normalized_company_screening(**kwargs) -> dict:
    """
    Create a normalized company screening dict with safe defaults.
    """
    return {
        "company_screening_coverage": kwargs.get("company_screening_coverage", "none"),
        "has_company_screening_hit": kwargs.get("has_company_screening_hit", None),
        "company_screening": kwargs.get("company_screening", {}),
    }


def create_normalized_screening_report(**kwargs) -> dict:
    """
    Create a normalized screening report dict with safe defaults.
    """
    result = {
        "provider": kwargs.get("provider", "sumsub"),
        "normalized_version": kwargs.get("normalized_version", "1.0"),
        "screened_at": kwargs.get("screened_at", ""),
        "any_pep_hits": kwargs.get("any_pep_hits", False),
        "any_sanctions_hits": kwargs.get("any_sanctions_hits", False),
        "total_persons_screened": kwargs.get("total_persons_screened", 0),
        "adverse_media_coverage": kwargs.get("adverse_media_coverage", "none"),
        "company_screening_coverage": kwargs.get("company_screening_coverage", "none"),
        "has_company_screening_hit": kwargs.get("has_company_screening_hit", None),
        "company_screening": kwargs.get("company_screening", {}),
        "director_screenings": kwargs.get("director_screenings", []),
        "ubo_screenings": kwargs.get("ubo_screenings", []),
        "overall_flags": kwargs.get("overall_flags", []),
        "total_hits": kwargs.get("total_hits", 0),
        "degraded_sources": kwargs.get("degraded_sources", []),
    }
    # Pass through any extra keys
    for key in kwargs:
        if key not in result:
            result[key] = kwargs[key]
    return result


def validate_normalized_report(report: dict) -> list:
    """
    Validate a normalized screening report dict.

    Returns a list of error messages. Empty list means the report is valid.
    """
    errors = []

    if not isinstance(report, dict):
        return ["report must be a dict"]

    # Required top-level fields
    required_fields = {
        "provider": str,
        "normalized_version": str,
        "screened_at": str,
        "any_pep_hits": bool,
        "any_sanctions_hits": bool,
        "total_persons_screened": int,
        "director_screenings": list,
        "ubo_screenings": list,
        "overall_flags": list,
        "total_hits": int,
        "degraded_sources": list,
    }

    for field, expected_type in required_fields.items():
        if field not in report:
            errors.append(f"missing required field: {field}")
        elif not isinstance(report[field], expected_type):
            errors.append(
                f"field '{field}' must be {expected_type.__name__}, "
                f"got {type(report[field]).__name__}"
            )

    # Coverage field validation
    coverage_fields = [
        ("adverse_media_coverage", "has_adverse_media_hit"),
        ("company_screening_coverage", "has_company_screening_hit"),
    ]

    for cov_field, hit_field in coverage_fields:
        if cov_field in report:
            cov_val = report[cov_field]
            if cov_val not in VALID_COVERAGE_VALUES:
                errors.append(
                    f"'{cov_field}' must be one of {VALID_COVERAGE_VALUES}, got '{cov_val}'"
                )
            # If coverage is "none", hit field must be null
            if cov_val == "none" and hit_field in report:
                if report[hit_field] is not None:
                    errors.append(
                        f"'{hit_field}' must be null when '{cov_field}' is 'none', "
                        f"got {report[hit_field]}"
                    )
        # Hit field type check
        if hit_field in report:
            val = report[hit_field]
            if val is not None and not isinstance(val, bool):
                errors.append(
                    f"'{hit_field}' must be bool or None, got {type(val).__name__}"
                )

    # Validate person screenings
    for list_field in ("director_screenings", "ubo_screenings"):
        if list_field in report and isinstance(report[list_field], list):
            for i, person in enumerate(report[list_field]):
                if not isinstance(person, dict):
                    errors.append(f"{list_field}[{i}] must be a dict")
                    continue

                # Person-level coverage semantics
                p_cov = person.get("adverse_media_coverage")
                if p_cov is not None and p_cov not in VALID_COVERAGE_VALUES:
                    errors.append(
                        f"{list_field}[{i}].adverse_media_coverage must be "
                        f"one of {VALID_COVERAGE_VALUES}, got '{p_cov}'"
                    )
                if p_cov == "none" and person.get("has_adverse_media_hit") is not None:
                    errors.append(
                        f"{list_field}[{i}].has_adverse_media_hit must be null "
                        f"when adverse_media_coverage is 'none'"
                    )

                # Hit field type checks
                for hit_f in ("has_pep_hit", "has_sanctions_hit", "has_adverse_media_hit"):
                    if hit_f in person:
                        val = person[hit_f]
                        if val is not None and not isinstance(val, bool):
                            errors.append(
                                f"{list_field}[{i}].{hit_f} must be bool or None, "
                                f"got {type(val).__name__}"
                            )

                if "is_rca" in person:
                    val = person["is_rca"]
                    if val is not None and not isinstance(val, bool):
                        errors.append(
                            f"{list_field}[{i}].is_rca must be bool or None, "
                            f"got {type(val).__name__}"
                        )

                if "pep_classes" in person:
                    val = person["pep_classes"]
                    if val is not None:
                        if not isinstance(val, list):
                            errors.append(
                                f"{list_field}[{i}].pep_classes must be list[str] or None, "
                                f"got {type(val).__name__}"
                            )
                        elif not all(isinstance(item, str) for item in val):
                            errors.append(
                                f"{list_field}[{i}].pep_classes must contain only strings"
                            )

    if "provenance" in report and report["provenance"] is not None:
        provenance = report["provenance"]
        try:
            if isinstance(provenance, TwoPassProvenance):
                pass
            elif isinstance(provenance, dict):
                TwoPassProvenance.model_validate(provenance)
            else:
                raise TypeError(type(provenance).__name__)
        except Exception as exc:
            errors.append(f"provenance must be compatible with TwoPassProvenance: {exc}")

    return errors
