"""Fixture corpus for the Phase 0B-1 Supervisor foundation.

Each builder seeds a real application into the test database and returns its
id, so the reproducibility harness exercises the actual assembler against real
rows rather than a hand-written bundle. The scenarios are chosen to cover every
branch that could plausibly break determinism or leak an availability state.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

#: The single instant every fixture review is evaluated at. Fixed, because
#: ``as_of`` is an input and a moving one would defeat the purpose.
AS_OF = "2026-07-31T00:00:00Z"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _insert_application(db, app_id: str, ref: str, **overrides: Any) -> None:
    row = {
        "id": app_id,
        "ref": ref,
        "company_name": "Fixture Holdings Ltd",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "ownership_structure": "direct",
        "status": "under_review",
        "risk_level": "MEDIUM",
        "risk_score": 48.5,
        "risk_dimensions": "{}",
        "prescreening_data": "{}",
    }
    row.update(overrides)
    columns = sorted(row)
    db.execute(
        f"INSERT INTO applications ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )
    db.commit()


def _risk_dimensions_blob(factors: list[dict[str, Any]]) -> str:
    """A stored ``risk_dimensions`` value shaped like ``compute_risk_score`` writes."""
    return json.dumps(
        {
            "d1": 2.0,
            "d2": 1.5,
            "factor_computation_evidence": {
                "schema_version": "risk-factor-evidence-v1",
                "dimensions": [
                    {
                        "dimension_id": "D1",
                        "dimension_score": 2.0,
                        "dimension_weight": 30.0,
                        "composite_contribution": 10.0,
                        "factor_keys": ["source_of_wealth", "entity_type"],
                    }
                ],
                "factors": factors,
            },
        }
    )


def _factor(key: str, dimension: str = "D1", score: int = 2, **extra: Any) -> dict[str, Any]:
    factor = {
        "factor_key": key,
        "dimension_id": dimension,
        "rule_score": score,
        "factor_weight": 16.6667,
        "normalized_value": key.replace("_", " "),
        "resolution_status": "resolved",
        "weighted_factor_contribution": 0.3333,
    }
    factor.update(extra)
    return factor


# ── Scenario builders ────────────────────────────────────────────────


def complete_standard(db) -> str:
    """Full case: parties, documents, memo, screening, decision, monitoring."""
    uid = _uid()
    app_id, ref = f"sf_complete_{uid}", f"ARF-2026-SFC{uid[:6].upper()}"
    _insert_application(
        db,
        app_id,
        ref,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="MEDIUM",
        risk_score=61.25,
        risk_config_version="risk_config:v7",
        elevation_reason_text="jurisdiction_risk_tier=high",
        risk_escalations=json.dumps(["elevated_jurisdiction", "declared_pep_present"]),
        risk_dimensions=_risk_dimensions_blob(
            [
                _factor("source_of_wealth", score=4),
                _factor("entity_type"),
                _factor("country_of_incorporation", dimension="D2", score=3),
            ]
        ),
        prescreening_data=json.dumps(
            {
                "screening_valid_until": "2026-10-29T00:00:00",
                "screening_report": {
                    "provider": "complyadvantage",
                    "screening_provider": "complyadvantage",
                    "screening_mode": "live",
                    "screened_at": "2026-07-01T09:00:00",
                    "company_screening": {
                        "company_name": "Fixture Holdings Ltd",
                        "provider": "complyadvantage",
                        "api_status": "live",
                        "matched": False,
                        "results": [],
                        "screened_at": "2026-07-01T09:00:00",
                        "screening_valid_until": "2026-10-29T00:00:00",
                    },
                    # v2: per-subject records, in reverse name order so the
                    # subjects[] ordering rule is exercised, not assumed.
                    "director_screenings": [
                        {
                            "person_name": "Zara Late",
                            "has_pep_hit": True,
                            "screening": {
                                "provider": "complyadvantage",
                                "api_status": "live",
                                "matched": True,
                                "results": [{"match_id": "m1"}],
                                "screened_at": "2026-07-01T09:00:00",
                                "screening_valid_until": "2026-10-29T00:00:00",
                            },
                        },
                        {
                            "person_name": "Jane Doe",
                            "screening": {
                                "provider": "complyadvantage",
                                "api_status": "live",
                                "matched": False,
                                "results": [],
                                "screened_at": "2026-07-01T09:00:00",
                                "screening_valid_until": "2026-10-29T00:00:00",
                            },
                        },
                    ],
                    "ubo_screenings": [
                        {
                            "person_name": "John Roe",
                            "screening": {
                                "provider": "complyadvantage",
                                "api_status": "live",
                                "matched": False,
                                "results": [],
                                "screened_at": "2026-07-01T09:00:00",
                                "screening_valid_until": "2026-10-29T00:00:00",
                            },
                        }
                    ],
                },
            }
        ),
        decided_at="2026-07-20T10:00:00",
        decision_by="officer-1",
    )

    db.execute(
        "INSERT INTO directors (id, application_id, person_key, full_name, nationality, "
        "is_pep, country_of_residence, pep_declaration) VALUES (?,?,?,?,?,?,?,?)",
        (f"dir_{uid}", app_id, "p-director-1", "Jane Doe", "MU", 1, "MU", json.dumps({"office": "x"})),
    )
    db.execute(
        "INSERT INTO ubos (id, application_id, person_key, full_name, nationality, "
        "ownership_pct, is_pep) VALUES (?,?,?,?,?,?,?)",
        (f"ubo_{uid}", app_id, "p-ubo-1", "John Roe", "ZA", 74.0, 0),
    )
    db.execute(
        "INSERT INTO intermediaries (id, application_id, person_key, entity_name, "
        "jurisdiction, ownership_pct) VALUES (?,?,?,?,?,?)",
        (f"int_{uid}", app_id, "p-inter-1", "Holdco SA", "LU", 26.0),
    )
    db.execute(
        "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, "
        "slot_key, verification_status, is_current, verification_results, verified_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            f"doc_{uid}",
            app_id,
            "cert_inc",
            "Certificate of Incorporation",
            "/tmp/x.pdf",
            "cert_inc",
            "verified",
            1,
            json.dumps(
                {
                    "checks": [
                        {"id": "DOC-02", "classification": "rule", "result": "pass",
                         "extracted_value": "Fixture Holdings Ltd"},
                        {"id": "DOC-01", "classification": "rule", "result": "pass",
                         "extracted_value": "12345"},
                    ],
                    "overall": "verified",
                }
            ),
            "2026-07-02T08:00:00",
        ),
    )
    db.execute(
        "INSERT INTO screening_reviews (application_id, subject_type, subject_name, "
        "disposition, disposition_code, reviewer_id) VALUES (?,?,?,?,?,?)",
        (app_id, "director", "Jane Doe", "cleared", "false_positive", "officer-2"),
    )
    db.execute(
        "INSERT INTO decision_records (id, application_ref, decision_type, risk_level, "
        "source, actor_user_id, actor_role, timestamp, override_flag, override_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            f"dec_{uid}",
            ref,
            "approve",
            "HIGH",
            "manual",
            "officer-1",
            "sco",
            "2026-07-20T10:00:00",
            1,
            "Senior override recorded for this case",
        ),
    )
    db.execute(
        "INSERT INTO periodic_reviews (application_id, client_name, risk_level, "
        "next_review_date, trigger_type, status) VALUES (?,?,?,?,?,?)",
        (app_id, "Fixture Holdings Ltd", "HIGH", "2027-01-20", "scheduled", "pending"),
    )
    db.execute(
        "INSERT INTO compliance_memos (application_id, version, memo_data, "
        "validation_status, validation_issues, quality_score) VALUES (?,?,?,?,?,?)",
        (
            app_id,
            2,
            json.dumps(
                {
                    "sections": {
                        "executive_summary": {"content": "generated prose"},
                        "compliance_decision": {"content": "generated prose"},
                        "risk_assessment": {"content": "generated prose"},
                    },
                    "metadata": {
                        "risk_rating": "HIGH",
                        "risk_score": 61.25,
                        "approval_recommendation": "REVIEW",
                        "ai_source": "deterministic",
                        "document_count": 1,
                        "verified_document_count": 1,
                        "documentation_complete": True,
                        "supervisor_status": "pending",
                        "edd_routing": {
                            "policy_version": "edd_routing_policy_v1",
                            "route": "edd",
                            "triggers": ["declared_pep_present", "high_or_very_high_risk"],
                        },
                        # v2: the stored EDD fact contract, shaped as
                        # memo_handler persists it. Trigger flags are seeded
                        # unsorted so the canonical ordering is exercised.
                        "agent5_input_contract": {
                            "final_risk_level": "HIGH",
                            "declared_pep_present": True,
                            "jurisdiction_risk_tier": "high",
                            "sector_risk_tier": "medium",
                            "ownership_transparency_status": "transparent",
                            "edd_trigger_flags": [
                                "risk_escalation:elevated_jurisdiction",
                                "declared_pep_present",
                            ],
                            "screening_terminality_summary": {
                                "terminal": True,
                                "has_terminal_match": False,
                                "has_non_terminal": False,
                                "canonical_state": "completed_clear",
                                "provider_mode": "live_provider",
                                "defensible_clear": True,
                                "approval_blocking": False,
                                "has_sandbox": False,
                                "has_simulated": False,
                                "has_formally_cleared_match": False,
                                "has_uncleared_completed_match": False,
                                "screening_result": "clear",
                                # Not a policy input — must be dropped by the
                                # projection rather than carried through.
                                "blocking_reasons": [],
                            },
                            # Contract keys the policy does not read; the
                            # projection must exclude these.
                            "decision_recommendation": "REVIEW",
                            "monitoring_tier": "Enhanced",
                            "sector_label": "Technology",
                        },
                    },
                }
            ),
            "pass",
            json.dumps([]),
            0.91,
        ),
    )
    db.execute(
        "INSERT INTO edd_cases (application_id, client_name, risk_level, stage, "
        "trigger_source) VALUES (?,?,?,?,?)",
        (app_id, "Fixture Holdings Ltd", "HIGH", "analysis", "policy_auto_route"),
    )
    db.commit()
    return app_id


def missing_optional_data(db) -> str:
    """Bare application: no parties, documents, memo, screening or decisions."""
    uid = _uid()
    app_id = f"sf_minimal_{uid}"
    _insert_application(db, app_id, f"ARF-2026-SFM{uid[:6].upper()}")
    return app_id


def null_values(db) -> str:
    """Nullable columns explicitly NULL, to prove null survives distinctly."""
    uid = _uid()
    app_id = f"sf_nulls_{uid}"
    _insert_application(
        db,
        app_id,
        f"ARF-2026-SFN{uid[:6].upper()}",
        country=None,
        sector=None,
        entity_type=None,
        ownership_structure=None,
        risk_level=None,
        risk_score=None,
        brn=None,
        onboarding_lane=None,
    )
    return app_id


def snapshot_incomplete(db) -> str:
    """Risk scored but no ``factor_computation_evidence`` — pre-schema case."""
    uid = _uid()
    app_id = f"sf_snapshot_{uid}"
    _insert_application(
        db,
        app_id,
        f"ARF-2026-SFS{uid[:6].upper()}",
        risk_dimensions=json.dumps({"d1": 2.0, "d2": 3.0}),
    )
    return app_id


def stable_floats(db) -> str:
    """Float values chosen to expose binary-representation drift."""
    uid = _uid()
    app_id = f"sf_floats_{uid}"
    _insert_application(
        db,
        app_id,
        f"ARF-2026-SFF{uid[:6].upper()}",
        risk_score=0.1 + 0.2,
        risk_dimensions=_risk_dimensions_blob(
            [
                _factor(
                    "monthly_volume",
                    dimension="D3",
                    weighted_factor_contribution=1 / 3,
                    factor_weight=0.1 + 0.2,
                ),
                _factor("transaction_complexity", dimension="D3",
                        weighted_factor_contribution=-0.0),
            ]
        ),
    )
    return app_id


def unordered_rows(db) -> str:
    """Parties and documents inserted in reverse sort order."""
    uid = _uid()
    app_id = f"sf_unordered_{uid}"
    _insert_application(db, app_id, f"ARF-2026-SFU{uid[:6].upper()}")
    for index in reversed(range(4)):
        db.execute(
            "INSERT INTO ubos (id, application_id, person_key, full_name, "
            "nationality, ownership_pct) VALUES (?,?,?,?,?,?)",
            (f"ubo{index}_{uid}", app_id, f"p-ubo-{index}", f"Owner {index}", "MU", 25.0),
        )
        db.execute(
            "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, "
            "slot_key, verification_status, is_current) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"doc{index}_{uid}",
                app_id,
                f"type_{index}",
                f"Doc {index}",
                "/tmp/x.pdf",
                f"slot_{index}",
                "verified",
                1,
            ),
        )
        db.execute(
            "INSERT INTO screening_reviews (application_id, subject_type, subject_name, "
            "disposition) VALUES (?,?,?,?)",
            (app_id, "director", f"Zed Person {index}", "cleared"),
        )
    db.commit()
    return app_id


#: Scenario name -> builder. ``credentials_absent``, ``dependency_gated`` and
#: ``policy_not_configured`` are the default state of the test environment, so
#: they are asserted against these same applications rather than needing their
#: own rows; dedicated tests flip each to its available branch.
SCENARIOS = {
    "complete_standard": complete_standard,
    "missing_optional_data": missing_optional_data,
    "null_values": null_values,
    "snapshot_incomplete": snapshot_incomplete,
    "stable_floats": stable_floats,
    "unordered_rows": unordered_rows,
}


# ── Synthetic probes ─────────────────────────────────────────────────
# Phase 0B-1 ships no probes. These exist only so the harness can prove that
# finding identifiers and review hashes are stable when findings are present.
# They assert nothing about compliance and must never leave the test suite.


def _draft(probe_id: str, category: str, status: str, availability: str, refs: list[str]):
    return {
        "probe_id": probe_id,
        "probe_version": "synthetic-v1",
        "category": category,
        "severity": "info",
        "status": status,
        "availability_status": availability,
        "confidence": 1.0,
        "claim": f"synthetic fixture finding from {probe_id}",
        "evidence_refs": refs,
        "source_modules": ["tests.supervisor_foundation_fixtures"],
        "why_it_matters": "exercises deterministic finding identity only",
        "regulatory_or_policy_basis": "internal_policy_only",
        "officer_question": "not a real question",
        "required_action": "none — synthetic fixture",
        "close_condition": "never closes — synthetic fixture",
    }


def synthetic_hit_probe(bundle, policy):
    """Emits one hit per active document, in deliberately reversed order."""
    documents = (bundle.get("documents") or {}).get("active") or []
    drafts = [
        _draft(
            "SYN-01",
            "documentation",
            "hit",
            "available",
            [f"document:{document.get('id')}", "policy_profile:unconfigured#none"],
        )
        for document in reversed(documents)
    ]
    return drafts


def synthetic_unavailable_probe(bundle, policy):
    """Emits one unavailable finding driven by the unconfigured policy."""
    return [
        _draft(
            "SYN-02",
            "policy_configuration",
            "unavailable",
            "policy_not_configured",
            ["policy_profile:unconfigured#ubo_identification_threshold"],
        )
    ]


SYNTHETIC_PROBES = (synthetic_hit_probe, synthetic_unavailable_probe)
