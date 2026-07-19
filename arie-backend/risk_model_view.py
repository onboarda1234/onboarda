"""Read-only projection of the risk model that the runtime actually executes.

This module does not define scores.  Configured values come from the same
validated ``risk_config`` object passed to ``compute_risk_score``; parser-owned
values are evaluated by the scorer itself.  The Back Office consumes this
projection so it cannot drift behind a second, UI-maintained model.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
import json
import logging
from typing import Any, Dict, Iterable, Mapping

from edd_routing_policy import ALL_TRIGGERS, POLICY_VERSION as EDD_POLICY_VERSION
from periodic_review_policy import ENHANCED_REVIEW_FLOOR_MONTHS, RISK_FREQUENCY_MONTHS
from risk_controlled_values import (
    FAMILY_RECORDS,
    REGISTRY_VERSION,
    UNRESOLVED_SECTOR_LABELS,
    mapping_fidelity_enabled,
)
from rule_engine import (
    ADVERSE_MEDIA_CLEAR_VALUES,
    ADVERSE_MEDIA_SCORE_2_KEYWORDS,
    ADVERSE_MEDIA_SCORE_4_KEYWORDS,
    DELIVERY_REMOTE_KEYWORDS,
    DELIVERY_SCORE_1_KEYWORDS,
    DELIVERY_SCORE_2_KEYWORDS,
    DELIVERY_SCORE_4_KEYWORDS,
    GATE0_DECLARED_PEP_SCORE,
    HIGH_RISK_SECTOR_KEYWORDS,
    OPAQUE_OWNERSHIP_KEYWORDS,
    RISK_LANE_MAP,
    RISK_SCORE_FLOORS,
    SERVICE_DOMESTIC_REQUIRED_KEYWORDS,
    SERVICE_SCORE_2_KEYWORDS,
    SERVICE_SCORE_3_KEYWORDS,
    SOURCE_OF_FUNDS_SCORE_MAP,
    SOURCE_OF_FUNDS_UNKNOWN_VALUES,
    SOURCE_OF_WEALTH_SCORE_MAP,
    SOURCE_OF_WEALTH_UNKNOWN_VALUES,
    _score_entity_type,
    classify_country,
    compute_risk_score,
    score_sector,
)
from security_hardening import (
    APPROVAL_ROUTE_BLOCKED,
    APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
    APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
    APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
    DIRECT_APPROVAL_RISK_LEVELS,
)


READ_ONLY_MESSAGE = (
    "This screen reflects the currently active runtime scoring model. "
    "Editing of the model will be introduced in a future governed release."
)

RISK_REPORT_EVIDENCE_UNAVAILABLE_MESSAGE = (
    "Risk report export is unavailable because the authoritative risk evidence "
    "is missing, incomplete, or stale. Recompute risk before exporting."
)


class RiskModelProjectionUnavailable(RuntimeError):
    """A controlled, non-sensitive projection failure for invalid runtime config."""


_REQUIRED_DIMENSION_IDS = ("D1", "D2", "D3", "D4", "D5")
_REQUIRED_SUBCRITERIA_COUNTS = {"D1": 6, "D2": 5, "D3": 3, "D4": 1, "D5": 2}


def _validate_projection_config(config: Mapping[str, Any]) -> None:
    """Reject incomplete projection inputs before runtime probes are attempted."""
    if not isinstance(config, Mapping):
        raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")
    dimensions = config.get("dimensions")
    thresholds = config.get("thresholds")
    if not isinstance(dimensions, list):
        raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")
    by_id = {
        str(item.get("id") or "").upper(): item
        for item in dimensions
        if isinstance(item, Mapping)
    }
    if len(dimensions) != len(_REQUIRED_DIMENSION_IDS) or tuple(sorted(by_id)) != tuple(sorted(_REQUIRED_DIMENSION_IDS)):
        raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")
    for dimension_id in _REQUIRED_DIMENSION_IDS:
        dimension = by_id[dimension_id]
        subcriteria = dimension.get("subcriteria")
        if (
            not isinstance(dimension.get("weight"), (int, float))
            or isinstance(dimension.get("weight"), bool)
            or not isinstance(subcriteria, list)
            or len(subcriteria) != _REQUIRED_SUBCRITERIA_COUNTS[dimension_id]
            or any(
                not isinstance(item, Mapping)
                or not str(item.get("name") or "").strip()
                or not isinstance(item.get("weight"), (int, float))
                or isinstance(item.get("weight"), bool)
                for item in subcriteria
            )
        ):
            raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")
    if not isinstance(thresholds, list) or not thresholds:
        raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")
    for field in ("country_risk_scores", "sector_risk_scores", "entity_type_scores"):
        if not isinstance(config.get(field), Mapping) or not _normalized_score_map(config.get(field)):
            raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")


_PROJECTION_PROBE_ACTIVE: ContextVar[bool] = ContextVar(
    "risk_model_projection_probe_active", default=False
)


class _ProjectionProbeLogFilter(logging.Filter):
    """Suppress synthetic scorer-probe logs without hiding real request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _PROJECTION_PROBE_ACTIVE.get()


_RUNTIME_LOGGER = logging.getLogger("arie")
if not any(isinstance(item, _ProjectionProbeLogFilter) for item in _RUNTIME_LOGGER.filters):
    _RUNTIME_LOGGER.addFilter(_ProjectionProbeLogFilter())


_FACTOR_LOCATION = {
    "entity_type": ("D1", 0, "d1"),
    "ownership": ("D1", 1, "d1"),
    "pep": ("D1", 2, "d1"),
    "adverse_media": ("D1", 3, "d1"),
    "source_of_wealth": ("D1", 4, "d1"),
    "source_of_funds": ("D1", 5, "d1"),
    "service_type": ("D3", 0, "d3"),
    "monthly_volume": ("D3", 1, "d3"),
    "complexity": ("D3", 2, "d3"),
    "sector": ("D4", 0, "d4"),
    "introduction": ("D5", 0, "d5"),
    "delivery_channel": ("D5", 1, "d5"),
}


def _normalized_score_map(value: Any) -> Dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    output: Dict[str, int] = {}
    for key, raw_score in value.items():
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            continue
        if score in {1, 2, 3, 4}:
            output[str(key)] = score
    return output


def _isolated_config(config: Mapping[str, Any], dimension_id: str, sub_index: int) -> Dict[str, Any]:
    isolated = deepcopy(dict(config or {}))
    dimensions = deepcopy(list(isolated.get("dimensions") or []))
    for dimension in dimensions:
        if str(dimension.get("id") or "").upper() != dimension_id:
            continue
        for index, subcriterion in enumerate(dimension.get("subcriteria") or []):
            subcriterion["weight"] = 100 if index == sub_index else 0
    isolated["dimensions"] = dimensions
    return isolated


def _lowest_country(country_scores: Mapping[str, int]) -> str:
    for country, score in sorted(country_scores.items()):
        if int(score) == 1:
            return country
    return ""


def _probe_runtime_factor(
    config: Mapping[str, Any],
    family: str,
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    dimension_id, sub_index, result_key = _FACTOR_LOCATION[family]
    country_scores = _normalized_score_map(config.get("country_risk_scores"))
    app = {
        "entity_type": "Listed Company on Regulated Exchange",
        "ownership_structure": "Simple — direct identifiable UBOs",
        "country": _lowest_country(country_scores),
        "sector": "Government / Public Sector",
        "directors": [],
        "ubos": [],
        "primary_service": "Multi-currency",
        "monthly_volume": "USD 50,000 to USD 500,000 per month",
        "transaction_complexity": "Standard — multi-currency, established corridors",
        "introduction_method": "Direct application — client initiated",
        "customer_interaction": "Video",
        "source_of_wealth": "Business revenue",
        "source_of_funds": "Company bank transfer",
        "adverse_media": "clear",
    }
    app.update(dict(payload or {}))
    token = _PROJECTION_PROBE_ACTIVE.set(True)
    try:
        result = compute_risk_score(
            app,
            config_override=_isolated_config(config, dimension_id, sub_index),
        )
    finally:
        _PROJECTION_PROBE_ACTIVE.reset(token)
    score = float((result.get("dimensions") or {}).get(result_key))
    return {
        "score": int(score) if score.is_integer() else score,
        "risk_level": result.get("level"),
        "escalations": list(result.get("escalations") or []),
        "requires_compliance_approval": bool(result.get("requires_compliance_approval")),
    }


def _ui_item(
    *,
    family: str,
    label: str,
    score: int | float,
    source: str,
    classification: str = "Correct",
    action: str = "Display from runtime projection",
    runtime_input: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    item = {
        "family": family,
        "label": label,
        "runtime_source": source,
        "runtime_score": score,
        "ui_score": score,
        "match": True,
        "classification": classification,
        "action": action,
    }
    if runtime_input is not None:
        item["runtime_input"] = dict(runtime_input)
    if metadata:
        item.update(dict(metadata))
    return item


def _controlled_catalog(config: Mapping[str, Any], family: str) -> list[Dict[str, Any]]:
    records = FAMILY_RECORDS[family]
    items: list[Dict[str, Any]] = []
    config_field = {
        "sector": "sector_risk_scores",
        "entity_type": "entity_type_scores",
    }.get(family)
    configured = _normalized_score_map(config.get(config_field)) if config_field else {}

    for label, record in records.items():
        if family == "sector":
            score = score_sector(label, configured)
            result = {"score": score}
            payload = {"sector": label}
            source = "rule_engine.score_sector + risk_config.sector_risk_scores"
        elif family == "entity_type":
            score = _score_entity_type(label, configured)
            result = {"score": score}
            payload = {"entity_type": label}
            source = "rule_engine._score_entity_type + risk_config.entity_type_scores"
        else:
            field = {
                "ownership": "ownership_structure",
                "complexity": "transaction_complexity",
                "introduction": "introduction_method",
                "monthly_volume": "monthly_volume",
            }[family]
            payload = {field: label}
            result = _probe_runtime_factor(config, family, payload)
            score = result["score"]
            source = f"rule_engine.compute_risk_score ({family} parser)"
        items.append(
            _ui_item(
                family=family,
                label=label,
                score=score,
                source=source,
                runtime_input=payload,
                metadata={
                    "controlled_id": record.get("id"),
                    "config_key": record.get("config_key") or "",
                    "locked_score": bool(record.get("locked_score")),
                    "escalations": result.get("escalations", []),
                    "requires_compliance_approval": result.get("requires_compliance_approval"),
                },
            )
        )

    if config_field:
        represented_keys = {
            str(record.get("config_key") or "").strip().casefold()
            for record in records.values()
            if str(record.get("config_key") or "").strip()
        }
        for key in sorted(configured):
            if key.strip().casefold() in represented_keys:
                continue
            score = (
                score_sector(key, configured)
                if family == "sector"
                else _score_entity_type(key, configured)
            )
            items.append(
                _ui_item(
                    family=family,
                    label=key,
                    score=score,
                    source=f"risk_config.{config_field}",
                    classification="Runtime only",
                    action="Show as a runtime-only configured lookup key",
                    runtime_input={"sector" if family == "sector" else "entity_type": key},
                )
            )
    return items


def _country_catalog(config: Mapping[str, Any]) -> list[Dict[str, Any]]:
    scores = _normalized_score_map(config.get("country_risk_scores"))
    return [
        _ui_item(
            family="country",
            label=country,
            score=classify_country(country, scores),
            source="rule_engine.classify_country + risk_config.country_risk_scores",
            runtime_input={"country": country},
            metadata={
                "applies_to": [
                    "Country of Incorporation",
                    "UBO Nationalities",
                    "Intermediary Shareholder Jurisdictions",
                    "Countries of Operation",
                    "Target Markets",
                ]
            },
        )
        for country in sorted(scores)
    ]


def _parser_rule_item(
    config: Mapping[str, Any],
    family: str,
    label: str,
    payload: Mapping[str, Any],
    *,
    catalog_key: str,
    match_type: str,
) -> Dict[str, Any]:
    result = _probe_runtime_factor(config, family, payload)
    return _ui_item(
        family=family,
        label=label,
        score=result["score"],
        source=f"rule_engine.compute_risk_score + rule_engine.{catalog_key}",
        runtime_input=payload,
        metadata={
            "runtime_catalog_key": catalog_key,
            "match_type": match_type,
            "escalations": result["escalations"],
            "requires_compliance_approval": result["requires_compliance_approval"],
        },
    )


def _keyword_catalog(
    config: Mapping[str, Any],
    family: str,
    field: str,
    catalog_key: str,
    score_map: Mapping[str, int],
) -> list[Dict[str, Any]]:
    return [
        _parser_rule_item(
            config,
            family,
            keyword,
            {field: keyword},
            catalog_key=catalog_key,
            match_type="contains",
        )
        for keyword in score_map
    ]


def _runtime_parser_catalog(config: Mapping[str, Any], family: str) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    if family == "adverse_media":
        groups = (
            ("ADVERSE_MEDIA_SCORE_4_KEYWORDS", ADVERSE_MEDIA_SCORE_4_KEYWORDS, "contains"),
            ("ADVERSE_MEDIA_SCORE_2_KEYWORDS", ADVERSE_MEDIA_SCORE_2_KEYWORDS, "contains"),
            ("ADVERSE_MEDIA_CLEAR_VALUES", ADVERSE_MEDIA_CLEAR_VALUES, "exact"),
        )
        for catalog_key, values, match_type in groups:
            for value in values:
                items.append(_parser_rule_item(
                    config,
                    family,
                    value,
                    {"adverse_media": value},
                    catalog_key=catalog_key,
                    match_type=match_type,
                ))
        items.append(_parser_rule_item(
            config,
            family,
            "Default branch (unrecognised or missing)",
            {"adverse_media": "runtime_projection_unrecognised"},
            catalog_key="compute_risk_score adverse-media default",
            match_type="default",
        ))
        return items

    if family in {"source_of_wealth", "source_of_funds"}:
        field = family
        if family == "source_of_wealth":
            score_map = SOURCE_OF_WEALTH_SCORE_MAP
            unknown_values = SOURCE_OF_WEALTH_UNKNOWN_VALUES
            map_key = "SOURCE_OF_WEALTH_SCORE_MAP"
            unknown_key = "SOURCE_OF_WEALTH_UNKNOWN_VALUES"
        else:
            score_map = SOURCE_OF_FUNDS_SCORE_MAP
            unknown_values = SOURCE_OF_FUNDS_UNKNOWN_VALUES
            map_key = "SOURCE_OF_FUNDS_SCORE_MAP"
            unknown_key = "SOURCE_OF_FUNDS_UNKNOWN_VALUES"
        items.extend(_keyword_catalog(config, family, field, map_key, score_map))
        for value in unknown_values:
            items.append(_parser_rule_item(
                config,
                family,
                value,
                {field: value},
                catalog_key=unknown_key,
                match_type="exact",
            ))
        items.append(_parser_rule_item(
            config,
            family,
            "Default unmatched declared value",
            {field: "runtime_projection_unmatched"},
            catalog_key="compute_risk_score declared-value default",
            match_type="default",
        ))
        return items

    if family == "service_type":
        items.append(_parser_rule_item(
            config,
            family,
            "domestic + single",
            {"primary_service": "domestic single"},
            catalog_key="SERVICE_DOMESTIC_REQUIRED_KEYWORDS",
            match_type="contains all",
        ))
        for catalog_key, values in (
            ("SERVICE_SCORE_2_KEYWORDS", SERVICE_SCORE_2_KEYWORDS),
            ("SERVICE_SCORE_3_KEYWORDS", SERVICE_SCORE_3_KEYWORDS),
        ):
            for value in values:
                items.append(_parser_rule_item(
                    config,
                    family,
                    value,
                    {"primary_service": value},
                    catalog_key=catalog_key,
                    match_type="contains",
                ))
        items.append(_parser_rule_item(
            config,
            family,
            "cross_border = true",
            {"primary_service": "", "cross_border": True},
            catalog_key="compute_risk_score cross_border branch",
            match_type="boolean",
        ))
        items.append(_parser_rule_item(
            config,
            family,
            "Default unmatched or missing value",
            {"primary_service": "runtime_projection_unmatched"},
            catalog_key="compute_risk_score service default",
            match_type="default",
        ))
        return items

    if family == "delivery_channel":
        for catalog_key, values in (
            ("DELIVERY_SCORE_1_KEYWORDS", DELIVERY_SCORE_1_KEYWORDS),
            ("DELIVERY_SCORE_2_KEYWORDS", DELIVERY_SCORE_2_KEYWORDS),
            ("DELIVERY_REMOTE_KEYWORDS", DELIVERY_REMOTE_KEYWORDS),
            ("DELIVERY_SCORE_4_KEYWORDS", DELIVERY_SCORE_4_KEYWORDS),
        ):
            for value in values:
                items.append(_parser_rule_item(
                    config,
                    family,
                    value,
                    {"customer_interaction": value},
                    catalog_key=catalog_key,
                    match_type="contains",
                ))
        elevated_country = next(
            (country for country, score in _normalized_score_map(config.get("country_risk_scores")).items() if score >= 3),
            "iran",
        )
        items.append(_parser_rule_item(
            config,
            family,
            "remote + incorporation country score >= 3",
            {"customer_interaction": DELIVERY_REMOTE_KEYWORDS[-1], "country": elevated_country},
            catalog_key="DELIVERY_REMOTE_KEYWORDS + classify_country",
            match_type="conditional",
        ))
        items.append(_parser_rule_item(
            config,
            family,
            "Default unmatched or missing value",
            {"customer_interaction": "runtime_projection_unmatched"},
            catalog_key="compute_risk_score delivery default",
            match_type="default",
        ))
        return items

    raise KeyError(f"Unsupported runtime parser family: {family}")


def _pep_catalog(config: Mapping[str, Any]) -> list[Dict[str, Any]]:
    payload = {
        "directors": [
            {
                "client_declared_pep": True,
                "pep_declaration": {
                    "client_declared_pep": True,
                    "pep_status": "declared_yes",
                    "pep_role_type": "runtime_structured_role",
                },
            }
        ]
    }
    result = _probe_runtime_factor(config, "pep", payload)
    return [
        _ui_item(
            family="pep",
            label="Any declared or officer-confirmed PEP role",
            score=result["score"],
            source="rule_engine._declared_pep_score_evidence",
            runtime_input=payload,
            metadata={
                "structured_evidence_path": "pep_declaration.pep_role_type",
                "approved_score_constant": GATE0_DECLARED_PEP_SCORE,
                "escalations": result["escalations"],
                "requires_compliance_approval": result["requires_compliance_approval"],
            },
        )
    ]


def _rule_rows(config: Mapping[str, Any], catalogs: Mapping[str, list[Dict[str, Any]]]) -> list[Dict[str, Any]]:
    volume_score_four = _required_catalog_item(
        catalogs,
        "monthly_volume",
        lambda item: item.get("runtime_score") == 4,
    )
    unsolicited = _required_catalog_item(
        catalogs,
        "introduction",
        lambda item: item.get("label") == "Unsolicited / unknown referral source",
    )
    return [
        {
            "id": "sector_score_4_high_floor",
            "category": "High floor",
            "label": "Sector score 4",
            "outcome": "Minimum final risk HIGH",
            "runtime_source": "rule_engine._is_high_risk_sector + apply_local_floor",
            "hidden_runtime_rule": False,
        },
        {
            "id": "sector_keyword_high_floor",
            "category": "High floor",
            "label": "High-risk sector keyword match",
            "outcome": "Minimum final risk HIGH",
            "runtime_source": "rule_engine.HIGH_RISK_SECTOR_KEYWORDS",
            "keywords": sorted(HIGH_RISK_SECTOR_KEYWORDS),
            "hidden_runtime_rule": True,
        },
        {
            "id": "opaque_ownership_high_floor",
            "category": "High floor",
            "label": "Opaque ownership",
            "outcome": "Minimum final risk HIGH",
            "runtime_source": "rule_engine._is_opaque_ownership + apply_local_floor",
            "keywords": sorted(OPAQUE_OWNERSHIP_KEYWORDS),
            "hidden_runtime_rule": False,
        },
        {
            "id": "declared_pep_high_floor",
            "category": "High floor",
            "label": "Declared or officer-confirmed PEP",
            "outcome": "Score 4 and minimum final risk HIGH",
            "runtime_source": "rule_engine._declared_pep_score_evidence + apply_local_floor",
            "hidden_runtime_rule": False,
        },
        {
            "id": "multi_service_max_risk",
            "category": "Service scoring",
            "label": "Multiple selected services",
            "outcome": "D3.1 service factor equals the maximum resolved selected-service score",
            "runtime_source": "rule_engine.resolve_selected_service_risk",
            "hidden_runtime_rule": False,
        },
        {
            "id": "monthly_volume_score_4_review",
            "category": "Compliance review",
            "label": "Monthly volume score 4",
            "outcome": "Compliance Review; no automatic tier floor",
            "runtime_source": "rule_engine.compute_risk_score + security_hardening.classify_approval_route",
            "reason_codes": volume_score_four.get("escalations") or [],
            "hidden_runtime_rule": False,
        },
        {
            "id": "unsolicited_referral_no_floor",
            "category": "No automatic floor",
            "label": "Unsolicited / unknown referral source",
            "outcome": "Score 4 only; no automatic HIGH floor",
            "runtime_source": "rule_engine.compute_risk_score introduction parser",
            "reason_codes": unsolicited.get("escalations") or [],
            "hidden_runtime_rule": False,
        },
        {
            "id": "country_score_3_high_floor",
            "category": "High floor",
            "label": "Country score 3 or higher",
            "outcome": "Minimum final risk HIGH",
            "runtime_source": "rule_engine._is_elevated_jurisdiction + apply_local_floor",
            "hidden_runtime_rule": True,
        },
        {
            "id": "country_score_4_very_high_floor",
            "category": "Very High floor",
            "label": "Country score 4 or sanctioned/FATF-black incorporation country",
            "outcome": "Minimum final risk VERY_HIGH",
            "runtime_source": "rule_engine._country_triggers_very_high_floor",
            "hidden_runtime_rule": True,
        },
        {
            "id": "material_screening_high_floor",
            "category": "Screening floor",
            "label": "Material unresolved screening concern",
            "outcome": "Minimum final risk HIGH; EDD trigger",
            "runtime_source": "rule_engine._has_material_screening_concern",
            "hidden_runtime_rule": True,
        },
        {
            "id": "screening_severe_combination",
            "category": "Screening floor",
            "label": "High-risk sector + elevated jurisdiction + screening concern, or multiple concerns",
            "outcome": "Minimum final risk VERY_HIGH",
            "runtime_source": "rule_engine.compute_risk_score elevation rule 3",
            "hidden_runtime_rule": True,
        },
        {
            "id": "unresolved_mapping_block",
            "category": "Approval block",
            "label": "Unresolved controlled mapping sentinel",
            "outcome": "Approval blocked until all unresolved mappings are cleared",
            "runtime_source": "risk_controlled_values.reconcile_mapping_staleness + security_hardening.classify_approval_route",
            "hidden_runtime_rule": True,
        },
        {
            "id": "composite_85_review",
            "category": "Compliance review",
            "label": "Composite score 85 or above",
            "outcome": "Compliance approval required",
            "runtime_source": "rule_engine.compute_risk_score escalation rule C",
            "hidden_runtime_rule": True,
        },
    ]


def _catalog_count(catalogs: Mapping[str, Iterable[Mapping[str, Any]]]) -> int:
    return sum(len(list(items)) for items in catalogs.values())


def _required_catalog_item(
    catalogs: Mapping[str, list[Dict[str, Any]]],
    family: str,
    predicate,
) -> Dict[str, Any]:
    items = catalogs.get(family)
    if not isinstance(items, list):
        raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")
    for item in items:
        if isinstance(item, Mapping) and predicate(item):
            return dict(item)
    raise RiskModelProjectionUnavailable("Runtime risk model projection is incomplete")


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _declared_pep_roles(app: Mapping[str, Any]) -> list[str]:
    """Read stored structured PEP declarations without evaluating PEP policy."""
    roles: list[str] = []
    for person in list(app.get("directors") or []) + list(app.get("ubos") or []):
        if not isinstance(person, Mapping):
            continue
        declaration = _json_object(person.get("pep_declaration"))
        status = str(person.get("pep_status") or declaration.get("pep_status") or "").strip().lower()
        declared = any(
            value is True
            for value in (
                person.get("client_declared_pep"),
                person.get("declared_pep"),
                person.get("officer_verified_pep"),
                person.get("verified_pep"),
                declaration.get("client_declared_pep"),
                declaration.get("declared_pep"),
                declaration.get("officer_verified_pep"),
                declaration.get("verified_pep"),
            )
        ) or status in {"declared_yes", "confirmed_pep"}
        if not declared:
            continue
        role = str(declaration.get("pep_role_type") or person.get("pep_role_type") or "declared_pep").strip()
        if role and role not in roles:
            roles.append(role)
    return roles


def _blocked_risk_report_evidence(*reason_codes: str) -> Dict[str, Any]:
    return {
        "available": False,
        "authoritative": True,
        "read_only": True,
        "status": "blocked",
        "message": RISK_REPORT_EVIDENCE_UNAVAILABLE_MESSAGE,
        "reason_codes": sorted({str(code) for code in reason_codes if code}),
    }


def build_authoritative_risk_report_evidence(
    app: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    approval_route: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Package stored application risk truth for read-only CSV/PDF exports.

    This function deliberately performs no scoring or weighted arithmetic. It
    joins persisted application outcomes to the validated runtime configuration
    that produced them and blocks export when provenance is incomplete or stale.
    """
    _validate_projection_config(config)
    app = dict(app or {})
    current_version = str(config.get("_config_version") or "").strip()
    stored_version = str(app.get("risk_config_version") or "").strip()
    computed_at = str(app.get("risk_computed_at") or "").strip()
    dimensions = _json_object(app.get("risk_dimensions"))
    computation_evidence = _json_object(dimensions.get("factor_computation_evidence"))
    escalations = [str(item) for item in _json_list(app.get("risk_escalations")) if str(item)]
    route = dict(approval_route or {})

    reasons: list[str] = []
    if not current_version:
        reasons.append("runtime_config_version_missing")
    if not stored_version:
        reasons.append("risk_config_version_missing")
    elif stored_version.startswith("stale:"):
        reasons.append("risk_evidence_stale")
    elif current_version and stored_version != current_version:
        reasons.append("risk_config_version_mismatch")
    if not computed_at:
        reasons.append("risk_computed_at_missing")
    expected_factor_keys = {
        "D1": {"entity_type", "ownership_structure", "pep_status", "adverse_media", "source_of_wealth", "source_of_funds"},
        "D2": {"country_of_incorporation", "ubo_nationalities", "intermediary_jurisdictions", "countries_of_operation", "target_markets"},
        "D3": {"service_type", "monthly_volume", "transaction_complexity"},
        "D4": {"industry_sector"},
        "D5": {"introduction_method", "delivery_channel"},
    }
    stored_factors = computation_evidence.get("factors")
    stored_dimensions = computation_evidence.get("dimensions")
    if computation_evidence.get("schema_version") != "risk-factor-evidence-v1":
        reasons.append("factor_evidence_schema_missing")
    if not isinstance(stored_factors, list):
        reasons.append("factor_evidence_missing")
        stored_factors = []
    if not isinstance(stored_dimensions, list):
        reasons.append("dimension_computation_evidence_missing")
        stored_dimensions = []
    required_factor_fields = {
        "dimension_id", "factor_key", "factor_label", "raw_value",
        "normalized_value", "rule_score", "factor_weight",
        "weighted_factor_contribution", "resolution_status",
        "rule_identifier", "evidence_source",
    }
    for item in stored_factors:
        if not isinstance(item, Mapping):
            reasons.append("factor_evidence_invalid")
            continue
        missing_fields = required_factor_fields - set(item)
        for field in sorted(missing_fields):
            reasons.append(
                f"factor_evidence_field_missing:{item.get('dimension_id', 'unknown')}:"
                f"{item.get('factor_key', 'unknown')}:{field}"
            )
    for dimension_id, factor_keys in expected_factor_keys.items():
        present = {
            str(item.get("factor_key") or "")
            for item in stored_factors
            if isinstance(item, Mapping) and item.get("dimension_id") == dimension_id
        }
        for missing_key in sorted(factor_keys - present):
            reasons.append(f"factor_evidence_missing:{dimension_id}:{missing_key}")
        matching_dimensions = [
            item for item in stored_dimensions
            if isinstance(item, Mapping) and item.get("dimension_id") == dimension_id
        ]
        if len(matching_dimensions) != 1:
            reasons.append(f"dimension_computation_evidence_missing:{dimension_id}")
        else:
            row = matching_dimensions[0]
            required_dimension_fields = {
                "dimension_id", "dimension_score", "dimension_weight",
                "rounding_adjustment", "composite_contribution", "factor_keys",
            }
            for field in sorted(required_dimension_fields - set(row)):
                reasons.append(
                    f"dimension_computation_field_missing:{dimension_id}:{field}"
                )

    try:
        score = float(app.get("risk_score"))
    except (TypeError, ValueError):
        score = None
        reasons.append("risk_score_missing")
    if score is not None and not 0 <= score <= 100:
        reasons.append("risk_score_invalid")

    tier = str(app.get("final_risk_level") or app.get("risk_level") or "").strip().upper().replace("-", "_").replace(" ", "_")
    if tier not in {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}:
        reasons.append("risk_tier_missing")
    if not route.get("route"):
        reasons.append("approval_route_missing")
    edd_route = str(app.get("onboarding_lane") or "").strip()
    if not edd_route:
        reasons.append("edd_route_missing")

    configured_dimensions = {
        str(item.get("id") or "").upper(): item
        for item in config.get("dimensions") or []
        if isinstance(item, Mapping)
    }
    dimension_rows: list[Dict[str, Any]] = []
    for index, dimension_id in enumerate(_REQUIRED_DIMENSION_IDS, start=1):
        stored_key = f"d{index}"
        try:
            stored_score = float(dimensions[stored_key])
        except (KeyError, TypeError, ValueError):
            reasons.append(f"{stored_key}_evidence_missing")
            continue
        if not 1 <= stored_score <= 4:
            reasons.append(f"{stored_key}_evidence_invalid")
            continue
        matching_computation = [
            item for item in stored_dimensions
            if isinstance(item, Mapping) and item.get("dimension_id") == dimension_id
        ]
        if matching_computation:
            try:
                if abs(float(matching_computation[0]["dimension_score"]) - stored_score) > 0.0001:
                    reasons.append(f"dimension_computation_score_mismatch:{dimension_id}")
                factor_total = sum(
                    float(item["weighted_factor_contribution"])
                    for item in stored_factors
                    if isinstance(item, Mapping) and item.get("dimension_id") == dimension_id
                )
                reproduced_dimension = factor_total + float(
                    matching_computation[0]["rounding_adjustment"]
                )
                if abs(reproduced_dimension - stored_score) > 0.0001:
                    reasons.append(f"factor_contribution_mismatch:{dimension_id}")
            except (KeyError, TypeError, ValueError):
                reasons.append(f"dimension_computation_evidence_invalid:{dimension_id}")
        runtime_dimension = configured_dimensions[dimension_id]
        dimension_rows.append({
            "id": dimension_id,
            "name": str(runtime_dimension.get("name") or dimension_id),
            "weight": runtime_dimension.get("weight"),
            "stored_score": stored_score,
            "subcriteria": deepcopy(list(runtime_dimension.get("subcriteria") or [])),
            "source": f"applications.risk_dimensions.{stored_key}",
        })

    if score is not None:
        try:
            reproduced_score = sum(
                float(item["composite_contribution"])
                for item in stored_dimensions
            ) + float(computation_evidence["policy_adjustment"])
            if abs(reproduced_score - score) > 0.0001:
                reasons.append("composite_computation_mismatch")
            if abs(float(computation_evidence["final_composite_score"]) - score) > 0.0001:
                reasons.append("final_composite_evidence_mismatch")
        except (KeyError, TypeError, ValueError):
            reasons.append("composite_computation_evidence_invalid")

    if reasons:
        return _blocked_risk_report_evidence(*reasons)

    floor_reasons = [item for item in escalations if item.startswith("floor_rule_")]
    return {
        "available": True,
        "authoritative": True,
        "read_only": True,
        "status": "ready",
        "source": "stored application risk evidence + validated runtime configuration",
        "config_version": stored_version,
        "risk_computed_at": computed_at,
        "application": {
            "score": score,
            "tier": tier,
            "dimensions": dimension_rows,
            "escalations": escalations,
            "floor_reasons": floor_reasons,
            "edd_route": edd_route,
            "approval_route": route,
        },
        "factor_evidence": deepcopy(stored_factors),
        "dimension_computation_evidence": deepcopy(stored_dimensions),
        "computation_evidence": deepcopy(computation_evidence),
    }


def _build_runtime_risk_model_view(config: Mapping[str, Any]) -> Dict[str, Any]:
    config = dict(config or {})
    _validate_projection_config(config)
    catalogs = {
        "sector": _controlled_catalog(config, "sector"),
        "entity_type": _controlled_catalog(config, "entity_type"),
        "ownership": _controlled_catalog(config, "ownership"),
        "complexity": _controlled_catalog(config, "complexity"),
        "introduction": _controlled_catalog(config, "introduction"),
        "monthly_volume": _controlled_catalog(config, "monthly_volume"),
        "country": _country_catalog(config),
        "pep": _pep_catalog(config),
        "adverse_media": _runtime_parser_catalog(config, "adverse_media"),
        "source_of_wealth": _runtime_parser_catalog(config, "source_of_wealth"),
        "source_of_funds": _runtime_parser_catalog(config, "source_of_funds"),
        "service_type": _runtime_parser_catalog(config, "service_type"),
        "delivery_channel": _runtime_parser_catalog(config, "delivery_channel"),
    }
    lane_b = [
        {
            "family": "sector",
            "label": label,
            "classification": "Lane B",
            "status": "Pending Risk Scoring Programme calibration",
            "active_runtime_entry": False,
            "runtime_score": None,
        }
        for label in sorted(UNRESOLVED_SECTOR_LABELS)
    ]
    rules = _rule_rows(config, catalogs)
    config_version = str(config.get("_config_version") or "")
    activation_enabled = bool(mapping_fidelity_enabled())
    return {
        "read_only": True,
        "message": READ_ONLY_MESSAGE,
        "runtime_source": {
            "config_loader": "rule_engine.load_risk_config",
            "scorer": "rule_engine.compute_risk_score",
            "controlled_registry": "risk_controlled_values.FAMILY_RECORDS",
            "controlled_registry_version": REGISTRY_VERSION,
            "config_version": config_version,
            "activation_flag": "ENABLE_RSMP_TIER0A_MAPPING_FIDELITY",
            "activation_enabled": activation_enabled,
            "parser_mode": "exact controlled mapping" if activation_enabled else "legacy parser",
        },
        "dimensions": deepcopy(list(config.get("dimensions") or [])),
        "thresholds": deepcopy(list(config.get("thresholds") or [])),
        "catalogs": catalogs,
        "rules": rules,
        "lane_b": {
            "message": "Pending Risk Scoring Programme calibration. Not currently part of the active runtime model.",
            "items": lane_b,
        },
        "edd_policy": {
            "version": EDD_POLICY_VERSION,
            "route": "edd",
            "triggers": list(ALL_TRIGGERS),
            "runtime_source": "edd_routing_policy.evaluate_edd_routing",
        },
        "approval_policy": {
            "direct_risk_levels": sorted(DIRECT_APPROVAL_RISK_LEVELS),
            "routes": [
                APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
                APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
                APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
                APPROVAL_ROUTE_BLOCKED,
            ],
            "runtime_source": "security_hardening.classify_approval_route",
        },
        "monitoring_policy": {
            "review_frequency_months": dict(RISK_FREQUENCY_MONTHS),
            "enhanced_review_floor_months": ENHANCED_REVIEW_FLOOR_MONTHS,
            "runtime_source": "periodic_review_policy.policy_snapshot_for_application",
            "note": "Post-approval monitoring does not change the initial composite score.",
        },
        "risk_lanes": dict(RISK_LANE_MAP),
        "risk_score_floors": dict(RISK_SCORE_FLOORS),
        "counts": {
            "active_ui_items": _catalog_count(catalogs),
            "runtime_rules": len(rules),
            "lane_b_items": len(lane_b),
        },
    }


def build_runtime_risk_model_view(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the complete read-only model view from runtime-owned sources."""
    try:
        return _build_runtime_risk_model_view(config)
    except RiskModelProjectionUnavailable:
        raise
    except (IndexError, KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError):
        raise RiskModelProjectionUnavailable(
            "Runtime risk model projection is incomplete"
        ) from None
