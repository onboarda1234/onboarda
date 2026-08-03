"""Shared builders for the review-engine suites.

Two ways to make a review, and both are needed.

``synthetic_review`` builds a finding set by hand. Status and severity rules
have to be tested at combinations the four merged probes cannot currently
produce — a ``low``-severity hit, a ``dependency_gated`` unavailable, twelve
critical concerns at once — and waiting for a probe that emits them would leave
those rules untested.

``real_review`` runs the actual probes over a real seeded database. Synthetic
findings prove the rules; real findings prove the rules apply to what the
platform actually emits. A suite with only the first tests the engine against
the test author's idea of a finding.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from supervisor_foundation import assemble_application_bundle, run_review
from supervisor_foundation.hashing import compute_finding_id
from supervisor_foundation.policy import unconfigured_policy
from supervisor_foundation.probes import PROBES
from supervisor_probe_fixtures import (
    AS_OF,
    add_memo,
    add_periodic_review,
    add_screening,
    approve,
    factor,
    person_entry,
    risk_dimensions,
    screening_record,
    seed_application,
)

SUBJECT_TYPE = "application"
SUBJECT_ID = "app-fixture"

#: A bundle carrying only what the engine reads from one: the schema version and
#: the dependency-availability section. Everything else it needs comes from the
#: findings, which is the property that keeps this layer a projection.
MINIMAL_BUNDLE: dict[str, Any] = {
    "availability": {
        "dependencies": {
            "gated_supervisor_package": "dependency_gated",
            "historic_decision_snapshot": "snapshot_incomplete",
            "memo": "available",
            "public_registry": "available",
            "risk_factor_evidence": "available",
            "screening_report": "available",
        }
    },
    "meta": {
        "as_of": AS_OF,
        "bundle_schema_version": "supervisor-bundle-v2",
        "subject_id": SUBJECT_ID,
        "subject_type": SUBJECT_TYPE,
    },
}


#: The same bundle with the decision state that establishes approval. Approval
#: wording is gated on this, so a test asserting an approval question must say
#: so in the bundle rather than in a severity.
APPROVED_BUNDLE: dict[str, Any] = {
    **{key: value for key, value in MINIMAL_BUNDLE.items()},
    "application": {"fields": {"status": "approved"}},
    "decision": {"records": [{"id": 1, "decision_type": "approve"}]},
}


def finding(
    *,
    probe_id: str = "P-04",
    category: str = "screening",
    severity: str = "high",
    status: str = "hit",
    availability_status: str = "available",
    primary_evidence_ref: str = "screening:app-fixture#subject:s1#provider_mode",
    evidence_refs: Sequence[str] | None = None,
    claim: str = "A claim about one subject.",
    required_action: str = "Do the thing.",
    close_condition: str = "The thing is done.",
    why_it_matters: str = "Because the control depends on it.",
    officer_question: str = "Was the thing done?",
    source_modules: Sequence[str] | None = None,
) -> dict[str, Any]:
    """One finding, shaped exactly as ``run_review`` normalises it.

    ``finding_id`` is computed with the real derivation rather than made up, so
    two fixture findings collide in the tests exactly where two real ones would.
    """
    refs = sorted({primary_evidence_ref, *(evidence_refs or ())})
    return {
        "availability_status": availability_status,
        "category": category,
        "claim": claim,
        "close_condition": close_condition,
        "confidence": 1.0,
        "created_at": AS_OF,
        "evidence_refs": refs,
        "finding_id": compute_finding_id(
            subject_type=SUBJECT_TYPE,
            subject_id=SUBJECT_ID,
            probe_id=probe_id,
            probe_version=f"{probe_id.lower().replace('-', '')}-v1",
            category=category,
            primary_evidence_ref=primary_evidence_ref,
        ),
        "officer_question": officer_question,
        "policy_version": "supervisor-review-v1",
        "primary_evidence_ref": primary_evidence_ref,
        "probe_id": probe_id,
        "probe_version": f"{probe_id.lower().replace('-', '')}-v1",
        "regulatory_or_policy_basis": "internal_policy_only",
        "required_action": required_action,
        "severity": severity,
        "source_modules": list(source_modules or ["fixture"]),
        "status": status,
        "subject_id": SUBJECT_ID,
        "subject_type": SUBJECT_TYPE,
        "why_it_matters": why_it_matters,
    }


def synthetic_review(
    findings: Sequence[Mapping[str, Any]],
    *,
    probes_run: int = 4,
    as_of: str = AS_OF,
) -> dict[str, Any]:
    """A ``run_review``-shaped envelope around hand-built findings."""
    return {
        "as_of": as_of,
        "findings": [dict(item) for item in findings],
        "finding_count": len(findings),
        "input_bundle_hash": "bundle-hash-fixture",
        "policy_version": "supervisor-review-v1",
        "probe_set_version": "probe-set-0b2-v1",
        "review_completeness": {"probes_run": probes_run},
        "review_hash": "review-hash-fixture",
        "review_schema_version": "supervisor-review-v1",
        "subject_id": SUBJECT_ID,
        "subject_type": SUBJECT_TYPE,
    }


def screening_subject_finding(key: str, **overrides: Any) -> dict[str, Any]:
    """A P-04 provider finding on one named screening subject."""
    defaults: dict[str, Any] = {
        "probe_id": "P-04",
        "category": "screening",
        "severity": "critical",
        "primary_evidence_ref": (
            f"screening:{SUBJECT_ID}#subject:{key}#provider_mode"
        ),
        "evidence_refs": [
            f"screening:{SUBJECT_ID}#subject:{key}",
            f"application:{SUBJECT_ID}#status",
        ],
        "claim": f"Screening subject '{key}' was screened against a sandbox.",
        "required_action": "Re-run screening against the live provider.",
        "close_condition": "A live terminal screening result exists.",
    }
    defaults.update(overrides)
    return finding(**defaults)


def real_review(db, application_id: str, *, as_of: str = AS_OF):
    """Assemble, run the four real probes, and return ``(review, bundle)``."""
    bundle = assemble_application_bundle(db, application_id, as_of=as_of)
    review = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
    return review, bundle


@pytest.fixture
def busy_case(db):
    """One approved case that trips all four probes at once.

    Shared rather than copied. Two suites previously built their own, and the
    copies had already diverged by one risk factor — so a change to either
    silently changed what only one suite exercised.
    """
    app_id = seed_application(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="MEDIUM",
        risk_config_version=None,
        elevation_reason_text=None,
        risk_dimensions=risk_dimensions(
            [
                factor("country_of_incorporation", rule_score=4, resolution_status="unresolved"),
                factor("service_selection", rule_score=4, resolution_status="unmatched"),
                factor("adverse_media", rule_score=1),
            ]
        ),
    )
    add_memo(db, app_id)
    record = screening_record(api_status="sandbox")
    add_screening(
        db,
        app_id,
        company=record,
        directors=[person_entry("Director One", record), person_entry("Director Two", record)],
        ubos=[person_entry("Ubo One", record, has_pep_hit=True)],
    )
    add_periodic_review(db, app_id, status="completed")
    approve(db, app_id)
    return app_id
