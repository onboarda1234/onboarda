#!/usr/bin/env python3
"""Offline, read-only RSMP Tier 0A mapping dry run.

The command consumes a JSON export and never imports ``db`` or opens a database
connection.  It calculates legacy and flag-enabled results in memory and writes
only the requested local report file.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, Mapping

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import environment
from edd_routing_policy import evaluate_edd_routing
from prescreening.risk_inputs import build_prescreening_risk_input
from risk_controlled_values import (
    ACTIVATION_FLAG,
    COUNTRY_EXACT_ALIASES,
    FAMILY_RECORDS,
    REGISTRY_VERSION,
    normalize_controlled_value,
    resolve_controlled_score,
)
from rule_engine import compute_risk_score
from security_hardening import classify_approval_route


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _application_key(value: Any) -> str:
    return hashlib.sha256(str(value or "missing").encode("utf-8")).hexdigest()[:16]


def _dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


@contextmanager
def _activation_state(enabled: bool):
    existed = ACTIVATION_FLAG in environment.flags._cache
    previous = environment.flags._cache.get(ACTIVATION_FLAG)
    environment.flags._cache[ACTIVATION_FLAG] = bool(enabled)
    try:
        yield
    finally:
        if existed:
            environment.flags._cache[ACTIVATION_FLAG] = previous
        else:
            environment.flags._cache.pop(ACTIVATION_FLAG, None)


def _risk_input(case: Mapping[str, Any]) -> Dict[str, Any]:
    application = _dict(case.get("application") or case)
    prescreening = _dict(application.get("prescreening_data"))
    return build_prescreening_risk_input(
        application=application,
        prescreening_data=prescreening,
        directors=list(case.get("directors") or []),
        ubos=list(case.get("ubos") or []),
        intermediaries=list(case.get("intermediaries") or []),
    )


def _mapping_evidence(scoring_input: Mapping[str, Any], config: Mapping[str, Any]) -> list[Dict[str, Any]]:
    values = {
        "sector": scoring_input.get("sector"),
        "entity_type": scoring_input.get("entity_type"),
        "ownership": scoring_input.get("ownership_structure"),
        "complexity": scoring_input.get("transaction_complexity") or scoring_input.get("payment_corridors"),
        "introduction": scoring_input.get("introduction_method"),
        "monthly_volume": scoring_input.get("monthly_volume") or scoring_input.get("expected_volume"),
    }
    configurable = {
        "sector": config.get("sector_risk_scores"),
        "entity_type": config.get("entity_type_scores"),
    }
    evidence = []
    for family, raw in values.items():
        resolution = resolve_controlled_score(
            family,
            raw,
            configured_scores=configurable.get(family),
            config_version=str(config.get("updated_at") or REGISTRY_VERSION),
        )
        evidence.append(resolution.to_dict())

    raw_country = scoring_input.get("country")
    normalized_country = normalize_controlled_value(raw_country)
    if not normalized_country:
        geo_status = "unresolved_missing"
        canonical = ""
    elif normalized_country in COUNTRY_EXACT_ALIASES:
        geo_status = "mapped_alias"
        canonical = COUNTRY_EXACT_ALIASES[normalized_country]
    else:
        geo_status = "deferred_tier1b"
        canonical = normalized_country
    evidence.append({
        "family": "incorporation_country",
        "raw_value": str(raw_country or ""),
        "normalized_value": normalized_country,
        "status": geo_status,
        "canonical_value": canonical,
        "config_version": str(config.get("updated_at") or REGISTRY_VERSION),
    })
    return evidence


def _policy_routes(risk: Mapping[str, Any]) -> Dict[str, Any]:
    edd = evaluate_edd_routing({
        "final_risk_level": risk.get("final_risk_level") or risk.get("level"),
        "declared_pep_present": bool(risk.get("declared_pep_present")),
        "sector_risk_tier": risk.get("sector_risk_tier"),
        "sector_label": risk.get("sector_label"),
        "jurisdiction_risk_tier": risk.get("jurisdiction_risk_tier"),
        "ownership_transparency_status": risk.get("ownership_transparency_status"),
        "screening_terminality_summary": {
            "terminal": True,
            "has_terminal_match": False,
            "has_non_terminal": False,
        },
        "edd_trigger_flags": [],
        "supervisor_mandatory_escalation": False,
        "supervisor_mandatory_escalation_reasons": [],
    })
    approval = classify_approval_route({
        "id": "offline-dry-run",
        "status": "compliance_review",
        "risk_level": risk.get("level"),
        "final_risk_level": risk.get("final_risk_level") or risk.get("level"),
        "risk_escalations": list(risk.get("escalations") or []),
        "prescreening_data": {
            "declared_pep": bool(risk.get("declared_pep_present")),
        },
    })
    return {
        "edd_route": edd.get("route"),
        "edd_triggers": edd.get("triggers") or [],
        "approval_route": approval.get("route"),
        "approval_reasons": approval.get("reasons") or [],
        "approval_escalation_reasons": approval.get("escalation_reasons") or [],
    }


def run_dry_run(payload: Mapping[str, Any]) -> Dict[str, Any]:
    config = _dict(payload.get("risk_config"))
    applications = list(payload.get("applications") or [])
    records = []
    unresolved = Counter()
    score_delta_count = 0
    tier_delta_count = 0
    edd_route_delta_count = 0
    approval_route_delta_count = 0

    for case in applications:
        app = _dict(case.get("application") or case)
        application_key = _application_key(app.get("id") or app.get("ref"))
        scoring_input = _risk_input(case)
        with _activation_state(False):
            legacy = compute_risk_score(scoring_input, config_override=config)
        with _activation_state(True):
            proposed = compute_risk_score(scoring_input, config_override=config)
        legacy_routes = _policy_routes(legacy)
        proposed_routes = _policy_routes(proposed)
        evidence = [
            dict(item)
            for item in (
                proposed.get("controlled_mapping_evidence")
                or _mapping_evidence(scoring_input, config)
            )
        ]
        # Runtime persistence retains the real application_id. The portable
        # founder-review artifact pseudonymizes it to avoid exporting a direct
        # staging identifier.
        for item in evidence:
            item["application_id"] = application_key
        for item in evidence:
            resolution_status = item.get("resolution_status", item.get("status"))
            if str(resolution_status or "").startswith("unresolved"):
                unresolved[(item.get("family"), item.get("normalized_value"))] += 1

        score_changed = legacy.get("score") != proposed.get("score")
        tier_changed = legacy.get("level") != proposed.get("level")
        score_delta_count += int(score_changed)
        tier_delta_count += int(tier_changed)
        edd_route_changed = legacy_routes["edd_route"] != proposed_routes["edd_route"]
        approval_route_changed = legacy_routes["approval_route"] != proposed_routes["approval_route"]
        edd_route_delta_count += int(edd_route_changed)
        approval_route_delta_count += int(approval_route_changed)
        records.append({
            "application_key": application_key,
            "is_fixture": bool(app.get("is_fixture")),
            "stored": {
                "score": app.get("risk_score"),
                "level": app.get("risk_level"),
                "config_version": app.get("risk_config_version"),
            },
            "legacy_recalculated": {
                "score": legacy.get("score"),
                "level": legacy.get("level"),
                "lane": legacy.get("lane"),
                "escalations": legacy.get("escalations"),
                **legacy_routes,
            },
            "proposed_flag_enabled": {
                "score": proposed.get("score"),
                "level": proposed.get("level"),
                "lane": proposed.get("lane"),
                "escalations": proposed.get("escalations"),
                **proposed_routes,
            },
            "score_changed": score_changed,
            "tier_changed": tier_changed,
            "edd_route_changed": edd_route_changed,
            "approval_route_changed": approval_route_changed,
            "mapping_evidence": evidence,
        })

    maps = {
        name: config.get(name) or {}
        for name in ("country_risk_scores", "sector_risk_scores", "entity_type_scores")
    }
    gate0 = payload.get("gate0_v4")
    return {
        "metadata": {
            "mode": "read_only_offline",
            "database_writes": 0,
            "activation_flag": ACTIVATION_FLAG,
            "activation_default": False,
            "registry_version": REGISTRY_VERSION,
            "live_config_version": config.get("updated_at"),
            "live_config_hashes": {name: _canonical_hash(value) for name, value in maps.items()},
            "code_registry_hash": _canonical_hash(FAMILY_RECORDS),
            "gate0_v4_status": "provided" if gate0 is not None else "not_provided",
            "gate0_v4_hash": _canonical_hash(gate0) if gate0 is not None else None,
        },
        "summary": {
            "active_scored_applications": len(records),
            "score_deltas": score_delta_count,
            "tier_deltas": tier_delta_count,
            "edd_route_deltas": edd_route_delta_count,
            "approval_route_deltas": approval_route_delta_count,
            "applications_with_unresolved_mappings": sum(
                1
                for record in records
                if any(
                    str(item.get("resolution_status", item.get("status")) or "").startswith("unresolved")
                    for item in record["mapping_evidence"]
                )
            ),
            "unresolved_counts": [
                {"family": family, "normalized_value": value, "count": count}
                for (family, value), count in sorted(unresolved.items())
            ],
        },
        "applications": records,
    }


def _parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    report = run_dry_run(payload)
    args.output_json.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
