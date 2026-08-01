"""Composable fixture builders for the Phase 0B-2 probe suites.

Every builder writes **real rows** and every assertion runs against a bundle
produced by the real assembler. Hand-written bundle literals were rejected: a
probe that passes against a dict nobody's database ever produces has proved
nothing. The cost is a slightly wordier fixture layer; the benefit is that a
change to how ``memo_handler`` persists its fact contract, or to how
``screening_state`` classifies a provider record, breaks these tests.

Compose what a scenario needs and nothing else — ``seed_application`` alone for
a bare case, plus ``add_memo`` / ``add_screening`` / ``add_periodic_review`` /
``add_edd_case`` / ``approve`` as the check under test requires.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Sequence

from supervisor_foundation import assemble_application_bundle, run_review
from supervisor_foundation.policy import unconfigured_policy

#: Fixed review instant for every probe fixture. ``as_of`` is an input, and a
#: moving one would make freshness and frequency assertions untestable.
AS_OF = "2026-07-31T00:00:00Z"

#: A valid ``risk_config_version``, so scenarios that are not about
#: configuration integrity do not accidentally trip P-02 check 2.
VALID_RISK_CONFIG = "risk_config:2026-01-15T00:00:00"


def uid() -> str:
    return uuid.uuid4().hex[:10]


# ── Application ──────────────────────────────────────────────────────


def seed_application(db, **overrides: Any) -> str:
    """Insert one application. ``overrides`` map directly onto columns.

    The defaults describe a case that trips no probe: medium risk, valid
    configuration, no elevation, still under review.
    """
    suffix = uid()
    row: dict[str, Any] = {
        "id": f"probe_{suffix}",
        "ref": f"ARF-2026-PRB{suffix[:6].upper()}",
        "company_name": "Probe Fixture Ltd",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "ownership_structure": "direct",
        "status": "under_review",
        "risk_level": "MEDIUM",
        "final_risk_level": "MEDIUM",
        "base_risk_level": "MEDIUM",
        "risk_score": 48.5,
        "risk_config_version": VALID_RISK_CONFIG,
        "risk_dimensions": risk_dimensions(),
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
    return row["id"]


def factor(
    key: str,
    *,
    dimension: str = "D1",
    rule_score: int = 2,
    resolution_status: str | None = "resolved",
    drop_resolution_status: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """One entry of ``factor_computation_evidence.factors``.

    ``drop_resolution_status`` omits the key entirely, which is the pre-schema
    shape — distinct from a key present with an unresolved value.
    """
    item: dict[str, Any] = {
        "factor_key": key,
        "dimension_id": dimension,
        "rule_score": rule_score,
        "factor_weight": 16.6667,
        "normalized_value": key.replace("_", " "),
        "weighted_factor_contribution": 0.3333,
    }
    if not drop_resolution_status:
        item["resolution_status"] = resolution_status
    item.update(extra)
    return item


def risk_dimensions(factors: Sequence[Mapping[str, Any]] | None = None) -> str:
    """A ``risk_dimensions`` blob carrying ``factor_computation_evidence``."""
    resolved = list(factors) if factors is not None else [factor("entity_type")]
    return json.dumps(
        {
            "d1": 2.0,
            "factor_computation_evidence": {
                "schema_version": "risk-factor-evidence-v1",
                "dimensions": [
                    {
                        "dimension_id": "D1",
                        "dimension_score": 2.0,
                        "dimension_weight": 30.0,
                        "composite_contribution": 10.0,
                        "factor_keys": [
                            str(item.get("factor_key")) for item in resolved
                        ],
                    }
                ],
                "factors": resolved,
            },
        }
    )


def no_factor_evidence() -> str:
    """A pre-schema ``risk_dimensions`` blob: scores, but no factor evidence."""
    return json.dumps({"d1": 2.0, "d2": 3.0})


# ── Screening ────────────────────────────────────────────────────────


def screening_record(
    *,
    api_status: str = "live",
    matched: bool = False,
    results: Sequence[Mapping[str, Any]] | None = None,
    screened_at: str | None = "2026-07-01T09:00:00",
    valid_until: str | None = "2026-10-29T00:00:00",
    provider: str = "complyadvantage",
    **extra: Any,
) -> dict[str, Any]:
    """One provider screening record, shaped as the platform stores it.

    ``api_status`` is what ``screening_state.provider_mode_from_record``
    classifies on: ``live`` → live provider, ``sandbox`` → sandbox,
    ``simulated`` → simulated fallback, ``not_configured``/``error``/``pending``
    → the corresponding unavailable modes.
    """
    record: dict[str, Any] = {
        "provider": provider,
        "api_status": api_status,
        "matched": matched,
        "results": list(results or []),
    }
    if screened_at is not None:
        record["screened_at"] = screened_at
    if valid_until is not None:
        record["screening_valid_until"] = valid_until
    record.update(extra)
    return record


def person_entry(name: str, record: Mapping[str, Any], **flags: Any) -> dict[str, Any]:
    """A director/UBO screening entry: a person wrapping a screening record.

    ``flags`` carries the material-hit booleans (``has_pep_hit``,
    ``has_sanctions_hit``, …) that ``screening_state`` treats as canonical.
    """
    entry: dict[str, Any] = {"person_name": name, "screening": dict(record)}
    entry.update(flags)
    return entry


def add_screening(
    db,
    application_id: str,
    *,
    company: Mapping[str, Any] | None = None,
    directors: Sequence[Mapping[str, Any]] = (),
    ubos: Sequence[Mapping[str, Any]] = (),
    screening_valid_until: str | None = "2026-10-29T00:00:00",
    **report_extra: Any,
) -> None:
    """Write a ``prescreening_data.screening_report`` onto the application."""
    report: dict[str, Any] = {
        "provider": "complyadvantage",
        "screening_provider": "complyadvantage",
        "screening_mode": "live",
        "screened_at": "2026-07-01T09:00:00",
    }
    if company is not None:
        report["company_screening"] = dict(company)
    if directors:
        report["director_screenings"] = [dict(entry) for entry in directors]
    if ubos:
        report["ubo_screenings"] = [dict(entry) for entry in ubos]
    report.update(report_extra)

    payload: dict[str, Any] = {"screening_report": report}
    if screening_valid_until is not None:
        payload["screening_valid_until"] = screening_valid_until
    db.execute(
        "UPDATE applications SET prescreening_data = ? WHERE id = ?",
        (json.dumps(payload), application_id),
    )
    db.commit()


def add_screening_review(
    db,
    application_id: str,
    *,
    subject_name: str,
    subject_type: str = "director",
    disposition: str = "cleared",
    disposition_code: str = "false_positive",
    reviewer_id: str = "officer-2",
    requires_four_eyes: bool = False,
    second_reviewer_id: str | None = None,
) -> None:
    """Record an officer disposition against a screening subject.

    The bundle matches reviews to subjects on ``(subject_type, name
    fingerprint)``, so ``subject_name`` must match the screened person's name
    exactly for the link to form.
    """
    db.execute(
        "INSERT INTO screening_reviews (application_id, subject_type, subject_name, "
        "disposition, disposition_code, reviewer_id, requires_four_eyes, "
        "second_reviewer_id) VALUES (?,?,?,?,?,?,?,?)",
        (
            application_id,
            subject_type,
            subject_name,
            disposition,
            disposition_code,
            reviewer_id,
            1 if requires_four_eyes else 0,
            second_reviewer_id,
        ),
    )
    db.commit()


# ── Memo, EDD, decision, monitoring ──────────────────────────────────

#: The fact contract ``memo_handler`` persists as
#: ``metadata.agent5_input_contract`` and feeds verbatim to the routing policy.
#: Defaults describe a standard-route case.
DEFAULT_ROUTING_FACTS = {
    "final_risk_level": "MEDIUM",
    "declared_pep_present": False,
    "jurisdiction_risk_tier": "low",
    "sector_risk_tier": "low",
    "ownership_transparency_status": "transparent",
    "edd_trigger_flags": [],
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
    },
}


def routing_facts(**overrides: Any) -> dict[str, Any]:
    facts = json.loads(json.dumps(DEFAULT_ROUTING_FACTS))
    summary_overrides = overrides.pop("screening_terminality_summary", None)
    if summary_overrides is not None:
        facts["screening_terminality_summary"].update(summary_overrides)
    facts.update(overrides)
    return facts


def add_memo(
    db,
    application_id: str,
    *,
    facts: Mapping[str, Any] | None = None,
    stored_route: str = "standard",
    policy_version: str = "edd_routing_policy_v1",
    triggers: Sequence[str] = (),
    include_contract: bool = True,
    metadata_extra: Mapping[str, Any] | None = None,
) -> None:
    """Write a compliance memo carrying the EDD routing contract and outcome.

    ``include_contract=False`` produces the pre-contract memo shape, which is
    how a real deployment's older memos look.
    """
    metadata: dict[str, Any] = {
        "risk_rating": "MEDIUM",
        "ai_source": "deterministic",
        "supervisor_status": "pending",
        "edd_routing": {
            "policy_version": policy_version,
            "route": stored_route,
            "triggers": list(triggers),
        },
    }
    if include_contract:
        metadata["agent5_input_contract"] = dict(
            facts if facts is not None else routing_facts()
        )
    if metadata_extra:
        metadata.update(metadata_extra)

    db.execute(
        "INSERT INTO compliance_memos (application_id, version, memo_data, "
        "validation_status, quality_score) VALUES (?,?,?,?,?)",
        (
            application_id,
            1,
            json.dumps({"sections": {}, "metadata": metadata}),
            "pass",
            0.9,
        ),
    )
    db.commit()


def add_edd_case(db, application_id: str, *, stage: str = "analysis") -> None:
    db.execute(
        "INSERT INTO edd_cases (application_id, client_name, risk_level, stage, "
        "trigger_source) VALUES (?,?,?,?,?)",
        (application_id, "Probe Fixture Ltd", "HIGH", stage, "policy_auto_route"),
    )
    db.commit()


def approve(
    db,
    application_id: str,
    *,
    ref: str | None = None,
    decided_at: str = "2026-07-20T10:00:00",
    risk_level: str = "MEDIUM",
) -> None:
    """Mark the application approved and record the decision.

    Both signals are written because severity in three probes turns on whether
    an officer actually relied on the defect, and the two are independent.
    """
    if ref is None:
        row = db.execute(
            "SELECT ref FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        ref = row["ref"]
    db.execute(
        "UPDATE applications SET status = 'approved', decided_at = ?, "
        "decision_by = 'officer-1' WHERE id = ?",
        (decided_at, application_id),
    )
    db.execute(
        "INSERT INTO decision_records (id, application_ref, decision_type, "
        "risk_level, source, actor_user_id, actor_role, timestamp) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            f"dec_{uid()}",
            ref,
            "approve",
            risk_level,
            "manual",
            "officer-1",
            "sco",
            decided_at,
        ),
    )
    db.commit()


def add_periodic_review(
    db,
    application_id: str,
    *,
    next_review_date: Any = "2027-01-20",
    last_review_date: Any = None,
    risk_level: str = "MEDIUM",
    policy_version: str | None = "periodic_review_policy_v1",
    status: str = "pending",
) -> None:
    db.execute(
        "INSERT INTO periodic_reviews (application_id, client_name, risk_level, "
        "next_review_date, last_review_date, policy_version, trigger_type, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            application_id,
            "Probe Fixture Ltd",
            risk_level,
            next_review_date,
            last_review_date,
            policy_version,
            "scheduled",
            status,
        ),
    )
    db.commit()


# ── Running probes ───────────────────────────────────────────────────


def bundle_for(db, application_id: str, *, as_of: str = AS_OF) -> dict[str, Any]:
    return assemble_application_bundle(db, application_id, as_of=as_of)


def run_probe(bundle: Mapping[str, Any], probe) -> list[dict[str, Any]]:
    """Run one probe through the real runner and return its findings.

    Going through ``run_review`` rather than calling the probe directly is
    deliberate: the runner is what validates the finding contract and stamps
    identity, so a draft that violates either fails the test that produced it.
    """
    review = run_review(bundle, policy=unconfigured_policy(), probes=(probe,))
    return review["findings"]


def only(findings: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    assert len(findings) == 1, f"expected exactly one finding, got {len(findings)}"
    return findings[0]


def claims(findings: Sequence[Mapping[str, Any]]) -> str:
    """All claim text joined, for substring assertions."""
    return " || ".join(str(finding.get("claim") or "") for finding in findings)
