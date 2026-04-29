"""ComplyAdvantage screening normalizer.

Converts validated CA Pydantic payloads into the canonical normalized screening
plain-dict schema used by RegMind.
"""

import hashlib
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from .models import (
    CAAlertResponse,
    CACustomerInput,
    CACustomerResponse,
    CAMediaIndicator,
    CAPEPIndicator,
    CAProfile,
    CARiskDetail,
    CASanctionIndicator,
    CAWatchlistIndicator,
    CAWorkflowResponse,
)
from .url_canonicalization import canonicalize_url

KNOWN_AML_KEYS = frozenset({
    "r_direct_sanctions_exposure",
    "r_sanctions_exposure_of_associate",
    "r_sanctions_exposure_parent",
    "r_sanctions_exposure_subsidiary",
    "r_pep_class_1",
    "r_pep_class_2",
    "r_pep_class_3",
    "r_pep_class_4",
    "r_rca",
    "r_terrorist_financing",
    "r_adverse_media_financial_crime",
    "r_adverse_media_violent_crime",
    "r_adverse_media_fraud",
    "r_adverse_media_corruption",
    "r_adverse_media_terrorism",
    "r_adverse_media_general",
    "r_watchlist",
    "r_law_enforcement",
    "r_regulatory_action",
    "r_insolvency",
    "r_disqualified_director",
    "r_state_owned_enterprise",
    "r_special_interest_person",
    "r_special_interest_entity",
    "r_reputational_risk",
    "r_warning",
    "r_fitness_probity",
    "r_organized_crime",
    "r_tax_crime",
    "r_human_trafficking",
    "r_environmental_crime",
    "r_cybercrime",
    "r_export_control",
})

KNOWN_PEP_CLASSES = frozenset({"PEP_CLASS_1", "PEP_CLASS_2", "PEP_CLASS_3", "PEP_CLASS_4"})


class ScreeningApplicationContext(BaseModel):
    """RegMind onboarding state passed to the normalizer."""

    application_id: str
    client_id: str
    screening_subject_kind: Literal["director", "ubo", "subject", "entity"]
    screening_subject_name: str
    screening_subject_person_key: Optional[str] = None
    declared_pep: Optional[bool] = None


class ResnapshotContext(BaseModel):
    """Webhook resnapshot metadata. Used only by normalize_single_pass."""

    webhook_type: str
    source_case_identifier: str
    received_at: str


class MergedMatch(BaseModel):
    """Internal representation of one match after merge."""

    risk: CARiskDetail
    surfaced_by_pass: Literal["strict", "relaxed", "both"]
    profile: Optional[CAProfile] = None
    profile_identifier: str = ""
    risk_id: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


def normalize_two_pass_screening(
    strict_workflow: CAWorkflowResponse,
    strict_alerts: list[CAAlertResponse],
    strict_deep_risks: dict[str, CARiskDetail],
    relaxed_workflow: CAWorkflowResponse,
    relaxed_alerts: list[CAAlertResponse],
    relaxed_deep_risks: dict[str, CARiskDetail],
    customer_input: CACustomerInput,
    customer_response: CACustomerResponse,
    application_context: ScreeningApplicationContext,
) -> dict:
    """Normalize a strict+relaxed create-and-screen flow to a plain dict."""
    strict = _attach_alert_profiles(strict_deep_risks, strict_alerts)
    relaxed = _attach_alert_profiles(relaxed_deep_risks, relaxed_alerts)
    merged_matches, provenance = merge_two_pass_results(strict, relaxed)
    provider_specific = _build_provider_specific_block(
        merged_matches,
        provenance,
        customer_input,
        customer_response,
        strict_workflow,
        relaxed_workflow,
        include_surfaced_by_pass=True,
    )
    return _build_report(merged_matches, application_context, provider_specific, provenance)


def normalize_single_pass(
    workflow: CAWorkflowResponse,
    alerts: list[CAAlertResponse],
    deep_risks: dict[str, CARiskDetail],
    customer_input: CACustomerInput,
    customer_response: CACustomerResponse,
    application_context: ScreeningApplicationContext,
    resnapshot_context: ResnapshotContext,
) -> dict:
    """Normalize one event-driven resnapshot pass to a plain dict."""
    attached = _attach_alert_profiles(deep_risks, alerts)
    matches = [
        MergedMatch(
            risk=risk,
            surfaced_by_pass="strict",
            profile=_risk_profile(risk),
            profile_identifier=_risk_profile_identifier(risk, key),
            risk_id=key,
        )
        for key, risk in attached.items()
    ]
    matches.sort(key=lambda m: m.profile_identifier)
    provider_specific = _build_provider_specific_block(
        matches,
        None,
        customer_input,
        customer_response,
        workflow,
        None,
        include_surfaced_by_pass=False,
        resnapshot_context=resnapshot_context,
    )
    return _build_report(matches, application_context, provider_specific, provenance=None)


def merge_two_pass_results(
    strict_deep: dict[str, CARiskDetail],
    relaxed_deep: dict[str, CARiskDetail],
) -> tuple[list[MergedMatch], dict]:
    """Deduplicate by CA profile identifier and tag the pass that surfaced it."""
    strict_by_profile = _risk_map_by_profile(strict_deep)
    relaxed_by_profile = _risk_map_by_profile(relaxed_deep)
    profile_ids = sorted(set(strict_by_profile) | set(relaxed_by_profile))
    merged = []
    for profile_id in profile_ids:
        strict_item = strict_by_profile.get(profile_id)
        relaxed_item = relaxed_by_profile.get(profile_id)
        if strict_item and relaxed_item:
            risk_id, risk = strict_item
            surfaced = "both"
        elif strict_item:
            risk_id, risk = strict_item
            surfaced = "strict"
        else:
            risk_id, risk = relaxed_item
            surfaced = "relaxed"
        merged.append(MergedMatch(
            risk=risk,
            surfaced_by_pass=surfaced,
            profile=_risk_profile(risk),
            profile_identifier=profile_id,
            risk_id=risk_id,
        ))
    provenance = {
        "strict_workflow_id": None,
        "relaxed_workflow_id": None,
        "strict_match_count": len(strict_by_profile),
        "relaxed_match_count": len(relaxed_by_profile),
        "merged_match_count": len(merged),
        "strict_only_count": sum(1 for m in merged if m.surfaced_by_pass == "strict"),
        "relaxed_only_count": sum(1 for m in merged if m.surfaced_by_pass == "relaxed"),
        "both_count": sum(1 for m in merged if m.surfaced_by_pass == "both"),
    }
    return merged, provenance


def compute_match_rollups(match: MergedMatch) -> dict:
    """Compute cross-provider rollups from one CA match."""
    has_pep = False
    has_sanctions = False
    has_media = False
    for indicator in _all_indicators(match.risk):
        key = _indicator_key(indicator)
        if isinstance(indicator, CAPEPIndicator):
            has_pep = True
        elif isinstance(indicator, CASanctionIndicator):
            has_sanctions = True
        elif isinstance(indicator, CAMediaIndicator):
            has_media = True
        elif isinstance(indicator, CAWatchlistIndicator) and key.startswith("r_sanctions_exposure"):
            has_sanctions = True
    profile = match.profile
    if profile is not None and profile.company is not None:
        is_rca = None
    elif profile is not None and profile.person is not None:
        is_rca = bool(profile.person.relationships.values)
    else:
        is_rca = None
    return {
        "has_pep_hit": has_pep,
        "has_sanctions_hit": has_sanctions,
        "has_adverse_media_hit": has_media,
        "is_rca": is_rca,
    }


def compute_ca_screening_hash(merged_matches: list[MergedMatch]) -> str:
    """Stable 32-char SHA-256 hash over normalized CA screening truth."""
    payload = [_hash_input_for_match(m) for m in sorted(merged_matches, key=lambda x: x.profile_identifier)]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]


def extract_pep_classes(match: MergedMatch) -> list[str] | None:
    classes = []
    for indicator in _all_indicators(match.risk):
        if isinstance(indicator, CAPEPIndicator):
            value = getattr(indicator.value, "class_", None)
            if value in KNOWN_PEP_CLASSES:
                classes.append(value)
    return sorted(set(classes)) or None


def derive_person_screening_from_match(
    match: MergedMatch,
    application_context: ScreeningApplicationContext,
    person_type: str,
) -> dict:
    rollups = compute_match_rollups(match)
    profile = match.profile
    nationality = ""
    if profile is not None and profile.person is not None:
        nationality = profile.person.nationality or (profile.person.countries[0] if profile.person.countries else "")
    return {
        "person_name": _profile_name(profile) or application_context.screening_subject_name,
        "person_type": person_type,
        "nationality": nationality,
        "declared_pep": "Yes" if application_context.declared_pep else "No",
        "has_pep_hit": rollups["has_pep_hit"],
        "has_sanctions_hit": rollups["has_sanctions_hit"],
        "has_adverse_media_hit": True if rollups["has_adverse_media_hit"] else None,
        "adverse_media_coverage": "full" if rollups["has_adverse_media_hit"] else "none",
        "screening": {"provider": "complyadvantage", "profile_identifier": match.profile_identifier},
        "screening_state": "completed_match",
        "requires_review": any((rollups["has_pep_hit"], rollups["has_sanctions_hit"], rollups["has_adverse_media_hit"])),
        "is_rca": rollups["is_rca"],
        "pep_classes": extract_pep_classes(match),
    }


def derive_company_screening_from_match(match: MergedMatch) -> dict:
    rollups = compute_match_rollups(match)
    return {
        "company_screening_coverage": "full",
        "has_company_screening_hit": any((rollups["has_sanctions_hit"], rollups["has_adverse_media_hit"])),
        "company_screening": {"provider": "complyadvantage", "profile_identifier": match.profile_identifier},
    }


def apply_top_level_rollups(director_screenings, ubo_screenings, company_screening) -> dict:
    persons = list(director_screenings) + list(ubo_screenings)
    adverse_hit = any(p.get("has_adverse_media_hit") for p in persons)
    if company_screening.get("has_company_screening_hit"):
        adverse_hit = adverse_hit or company_screening.get("company_screening", {}).get("has_adverse_media_hit", False)
    return {
        "any_pep_hits": any(p.get("has_pep_hit") for p in persons),
        "any_sanctions_hits": any(p.get("has_sanctions_hit") for p in persons) or bool(company_screening.get("has_company_screening_hit")),
        "total_persons_screened": len(persons),
        "adverse_media_coverage": "full" if adverse_hit else "none",
        "has_adverse_media_hit": True if adverse_hit else None,
        "company_screening_coverage": company_screening.get("company_screening_coverage", "none"),
        "has_company_screening_hit": company_screening.get("has_company_screening_hit"),
    }


def _build_report(matches, context, provider_specific, provenance):
    director_screenings = []
    ubo_screenings = []
    company_screening = {
        "company_screening_coverage": "none",
        "has_company_screening_hit": None,
        "company_screening": {},
    }
    if context.screening_subject_kind == "entity":
        if matches:
            company_screening = derive_company_screening_from_match(matches[0])
    else:
        person_type = "ubo" if context.screening_subject_kind == "ubo" else "director"
        if matches:
            person = derive_person_screening_from_match(matches[0], context, person_type)
        else:
            person = _empty_person_screening(context, person_type)
        if person_type == "ubo":
            ubo_screenings.append(person)
        else:
            director_screenings.append(person)
    rollups = apply_top_level_rollups(director_screenings, ubo_screenings, company_screening)
    report = {
        "provider": "complyadvantage",
        "normalized_version": "2.0",
        "screened_at": "",
        **rollups,
        "company_screening": company_screening.get("company_screening", {}),
        "director_screenings": director_screenings,
        "ubo_screenings": ubo_screenings,
        "overall_flags": _overall_flags(matches),
        "total_hits": len(matches),
        "degraded_sources": [],
        "any_non_terminal_subject": False,
        "company_screening_state": "completed_match" if company_screening.get("has_company_screening_hit") else "completed_clear",
        "provider_specific": {"complyadvantage": provider_specific},
        "source_screening_report_hash": compute_ca_screening_hash(matches),
    }
    if provenance is not None:
        report["provenance"] = provenance
    return report


def _empty_person_screening(context, person_type):
    return {
        "person_name": context.screening_subject_name,
        "person_type": person_type,
        "nationality": "",
        "declared_pep": "Yes" if context.declared_pep else "No",
        "has_pep_hit": False,
        "has_sanctions_hit": False,
        "has_adverse_media_hit": None,
        "adverse_media_coverage": "none",
        "screening": {"provider": "complyadvantage"},
        "screening_state": "completed_clear",
        "requires_review": bool(context.declared_pep),
        "is_rca": False,
        "pep_classes": None,
    }


def _attach_alert_profiles(deep_risks, alerts):
    by_id = dict(deep_risks)
    for alert in alerts:
        profile = alert.profile
        risk_values = list(alert.risk_details.values)
        risk = by_id.get(alert.identifier) or (risk_values[0] if risk_values else None)
        if risk is None:
            risk = CARiskDetail()
        _set_extra(risk, "_ca_profile", profile)
        by_id[alert.identifier] = risk
    return by_id


def _risk_map_by_profile(deep):
    result = {}
    for key, risk in deep.items():
        profile_id = _risk_profile_identifier(risk, key)
        result[profile_id] = (key, risk)
    return result


def _risk_profile_identifier(risk, fallback):
    profile = _risk_profile(risk)
    if profile is not None:
        return profile.identifier
    return str(fallback)


def _risk_profile(risk):
    return getattr(risk, "_ca_profile", None) or getattr(risk, "profile", None)


def _set_extra(model, key, value):
    object.__setattr__(model, key, value)


def _all_indicators(risk):
    indicators = []
    for detail in risk.values:
        indicators.extend(detail.indicators)
    return indicators


def _indicator_key(indicator):
    risk_type = getattr(indicator, "risk_type", None)
    return getattr(risk_type, "key", "") or ""


def _indicator_label(indicator):
    risk_type = getattr(indicator, "risk_type", None)
    return (
        getattr(risk_type, "label", None)
        or getattr(risk_type, "name", None)
        or getattr(risk_type, "key", None)
    )


def _profile_name(profile):
    if profile is None:
        return ""
    if profile.person is not None and profile.person.names.values:
        return profile.person.names.values[0].name
    if profile.company is not None and profile.company.names.values:
        return profile.company.names.values[0].name
    return ""


def _overall_flags(matches):
    flags = []
    for match in matches:
        rollups = compute_match_rollups(match)
        name = _profile_name(match.profile) or match.profile_identifier
        if rollups["has_sanctions_hit"]:
            flags.append(f"ComplyAdvantage sanctions/watchlist hit: {name}")
        if rollups["has_pep_hit"]:
            flags.append(f"ComplyAdvantage PEP hit: {name}")
        if rollups["has_adverse_media_hit"]:
            flags.append(f"ComplyAdvantage adverse media hit: {name}")
    return flags


def _build_provider_specific_block(
    merged_matches,
    provenance,
    customer_input,
    customer_response,
    strict_workflow,
    relaxed_workflow,
    include_surfaced_by_pass,
    resnapshot_context=None,
):
    block = {
        "customer_input": _dump(customer_input),
        "customer_response": _dump(customer_response),
        "workflows": {"strict": _dump(strict_workflow)},
        "matches": [_provider_match(m, include_surfaced_by_pass) for m in merged_matches],
    }
    if relaxed_workflow is not None:
        block["workflows"]["relaxed"] = _dump(relaxed_workflow)
    if provenance is not None:
        block["provenance"] = provenance
    if resnapshot_context is not None:
        block["resnapshot"] = _dump(resnapshot_context)
    return block


def _provider_match(match, include_surfaced_by_pass):
    data = {
        "profile_identifier": match.profile_identifier,
        "risk_id": match.risk_id,
        "profile": _dump(match.profile),
        "risk_detail": _dump(match.risk),
        "rollups": compute_match_rollups(match),
        "pep_classes": extract_pep_classes(match),
        "relationships": _relationships(match.profile),
        "indicators": [_indicator_payload(i) for i in _all_indicators(match.risk)],
    }
    if include_surfaced_by_pass:
        data["surfaced_by_pass"] = match.surfaced_by_pass
    raw_extras = _match_raw_extras(match)
    if raw_extras:
        data["raw_extras"] = raw_extras
    return data


def _relationships(profile):
    if profile is None or profile.person is None:
        return []
    return [_dump(rel) for rel in profile.person.relationships.values]


def _indicator_payload(indicator):
    base = {
        "type": indicator.__class__.__name__,
        "taxonomy_key": _indicator_key(indicator),
        "taxonomy_label": _indicator_label(indicator),
    }
    if isinstance(indicator, CASanctionIndicator):
        base["value"] = _dump(indicator.value)
    elif isinstance(indicator, CAWatchlistIndicator):
        base["value"] = _dump(indicator.value)
    elif isinstance(indicator, CAPEPIndicator):
        base["value"] = _dump(indicator.value)
    elif isinstance(indicator, CAMediaIndicator):
        base["value"] = _canonicalize_article(indicator.value)
    else:
        base["value"] = _dump(getattr(indicator, "value", {}))
    return base


def _match_raw_extras(match: MergedMatch) -> dict[str, Any]:
    """Collect per-match unknown CA fields by source model family.

    Returns a sparse raw_extras block with profile, risk_detail, and indicators
    keys only when those source model trees contain Pydantic extra fields.
    """

    raw_extras = {}
    profile_extras = _collect_raw_extras(match.profile)
    if profile_extras:
        raw_extras["profile"] = profile_extras
    risk_detail_extras = _collect_raw_extras(match.risk)
    if risk_detail_extras:
        raw_extras["risk_detail"] = risk_detail_extras
    indicator_extras = {}
    for index, indicator in enumerate(_all_indicators(match.risk)):
        extras = _collect_raw_extras(indicator)
        if extras:
            indicator_extras[str(index)] = extras
    if indicator_extras:
        raw_extras["indicators"] = indicator_extras
    return raw_extras


def _collect_raw_extras(value: Any) -> dict[str, Any]:
    """Recursively collect only __pydantic_extra__ values from model trees."""

    if isinstance(value, BaseModel):
        result = {}
        extras = getattr(value, "__pydantic_extra__", None) or {}
        if extras:
            result.update({key: _jsonable_extra(raw) for key, raw in extras.items()})
        for field_name in value.__class__.model_fields:
            nested = _collect_raw_extras(getattr(value, field_name, None))
            if nested:
                result[field_name] = nested
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            nested = _collect_raw_extras(item)
            if nested:
                result[str(index)] = nested
        return result
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            nested = _collect_raw_extras(item)
            if nested:
                result[str(key)] = nested
        return result
    return {}


def _jsonable_extra(value: Any) -> Any:
    """Convert preserved extra values into JSON-compatible plain values."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [_jsonable_extra(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable_extra(item) for key, item in value.items()}
    return value


def _canonicalize_article(article) -> dict:
    raw_url = article.url or ""
    data = _dump(article)
    data["raw_url"] = raw_url
    data["canonical_url"] = canonicalize_url(raw_url)
    data["snippets"] = _preserve_snippet_objects(article.snippets)
    return data


def _preserve_snippet_objects(snippets) -> list[dict]:
    return [{"text": snippet.text} for snippet in snippets]


def _hash_input_for_match(match) -> dict:
    return {
        "profile_id": match.profile_identifier,
        "subject_kind": match.profile.subject_kind if match.profile is not None else "unknown",
        "risk_types": sorted(_risk_type_keys(match.risk)),
        "rollups": compute_match_rollups(match),
        "pep_classes": extract_pep_classes(match) or [],
        "relationships": sorted(_relationship_signature(r) for r in _relationships(match.profile)),
        "sanctions": sorted(_sanction_value_signature(i.value) for i in _all_indicators(match.risk) if isinstance(i, CASanctionIndicator)),
        "watchlists": sorted(_watchlist_value_signature(i.value) for i in _all_indicators(match.risk) if isinstance(i, CAWatchlistIndicator)),
        "peps": sorted(_pep_value_signature(i.value) for i in _all_indicators(match.risk) if isinstance(i, CAPEPIndicator)),
        "media": sorted(_media_value_signature(i.value) for i in _all_indicators(match.risk) if isinstance(i, CAMediaIndicator)),
    }


def _risk_type_keys(risk):
    keys = []
    for detail in risk.values:
        keys.append(detail.risk_type.key)
    return keys


def _relationship_signature(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sanction_value_signature(v) -> str:
    return _signature({"program": v.program, "authority": v.authority})


def _watchlist_value_signature(v) -> str:
    return _signature({"list_name": v.list_name, "authority": v.authority})


def _pep_value_signature(v) -> str:
    return _signature({"class": v.class_, "position": v.position, "country": v.country})


def _media_value_signature(v) -> str:
    return _signature({
        "title": v.title,
        "canonical_url": canonicalize_url(v.url or ""),
        "snippets": _preserve_snippet_objects(v.snippets),
    })


def _signature(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _dump(model):
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True)
    return model
