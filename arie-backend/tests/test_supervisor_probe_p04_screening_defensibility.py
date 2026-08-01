"""P-04 — screening reliance defensibility.

Five checks, all per-subject except adverse-media absence. This suite carries
the identity-collision regression for the whole probe set: P-04 is the only
probe that can fire four times on one subject and once per subject across many,
so if finding identity is fragile it breaks here first.
"""

from __future__ import annotations

import pytest

from supervisor_foundation.probes import screening_defensibility
from supervisor_probe_fixtures import (
    AS_OF,
    add_screening,
    add_screening_review,
    approve,
    bundle_for,
    claims,
    factor,
    only,
    person_entry,
    risk_dimensions,
    run_probe,
    screening_record,
    seed_application,
)

PROBE = screening_defensibility.probe

CLEAN = screening_record()


def _findings_for(db, app_id):
    return run_probe(bundle_for(db, app_id), PROBE)


# ── Check 1: provider defensibility ──────────────────────────────────


@pytest.mark.parametrize(
    "api_status,expected_phrase",
    [
        ("sandbox", "a sandbox provider"),
        ("simulated", "a simulated fallback rather than a provider call"),
    ],
)
def test_undefensible_provider_mode_is_a_hit(db, api_status, expected_phrase):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(api_status=api_status))
    findings = _findings_for(db, app_id)
    text = claims(findings)
    assert expected_phrase in text
    assert any(finding["severity"] == "high" for finding in findings)


def test_sandbox_screening_relied_on_for_approval_is_critical(db):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(api_status="sandbox"))
    approve(db, app_id)
    findings = _findings_for(db, app_id)
    provider = next(f for f in findings if "sandbox provider" in f["claim"])
    assert provider["severity"] == "critical"
    assert "approved relying on this result" in provider["claim"]


@pytest.mark.parametrize(
    "api_status,expected_availability",
    [
        ("not_configured", "credentials_absent"),
        ("error", "data_absent"),
        ("pending", "data_absent"),
    ],
)
def test_unavailable_provider_modes_are_never_clear(db, api_status,
                                                    expected_availability):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(api_status=api_status))
    findings = _findings_for(db, app_id)
    provider = next(
        f for f in findings if "Screening provider state" in f["claim"]
    )
    assert provider["status"] == "unavailable"
    assert provider["availability_status"] == expected_availability
    assert provider["status"] != "clear"


# ── Check 2: terminality ─────────────────────────────────────────────


def test_non_terminal_subject_is_reported_as_incomplete(db):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(api_status="pending"))
    findings = _findings_for(db, app_id)
    assert "did not complete" in claims(findings)


def test_terminal_states_match_the_authoritative_set():
    """The probe's terminal set is the platform's, not a private opinion."""
    from screening_state import TERMINAL_STATES as AUTHORITATIVE

    assert screening_defensibility.TERMINAL_STATES == frozenset(AUTHORITATIVE)


def test_approved_case_with_an_unscreened_subject_is_critical(db):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(api_status="pending"))
    approve(db, app_id)
    findings = _findings_for(db, app_id)
    terminality = next(f for f in findings if "did not complete" in f["claim"])
    assert terminality["severity"] == "critical"


# ── Check 3: match disposition ───────────────────────────────────────


def test_material_match_without_a_disposition_is_a_hit(db):
    app_id = seed_application(db)
    add_screening(
        db,
        app_id,
        directors=[
            person_entry(
                "Zara Late",
                screening_record(matched=True, results=[{"match_id": "m1"}]),
                has_pep_hit=True,
            )
        ],
    )
    findings = _findings_for(db, app_id)
    finding = next(f for f in findings if f["category"] == "sanctions")
    assert "no officer disposition is recorded" in finding["claim"]
    assert finding["severity"] == "high"


def test_dispositioned_match_clears_the_check(db):
    app_id = seed_application(db)
    add_screening(
        db,
        app_id,
        directors=[
            person_entry(
                "Zara Late",
                screening_record(matched=True, results=[{"match_id": "m1"}]),
                has_pep_hit=True,
            )
        ],
    )
    add_screening_review(db, app_id, subject_name="Zara Late")
    findings = _findings_for(db, app_id)
    assert not [f for f in findings if f["category"] == "sanctions"]


def test_four_eyes_requirement_raised_and_not_met(db):
    app_id = seed_application(db)
    add_screening(
        db,
        app_id,
        directors=[
            person_entry(
                "Zara Late",
                screening_record(matched=True, results=[{"match_id": "m1"}]),
                has_pep_hit=True,
            )
        ],
    )
    add_screening_review(
        db, app_id, subject_name="Zara Late", requires_four_eyes=True,
        second_reviewer_id=None,
    )
    finding = next(
        f for f in _findings_for(db, app_id) if f["category"] == "sanctions"
    )
    assert "no second reviewer is recorded" in finding["claim"]
    assert finding["severity"] == "high"


def test_four_eyes_requirement_met_clears_the_check(db):
    app_id = seed_application(db)
    add_screening(
        db,
        app_id,
        directors=[
            person_entry(
                "Zara Late",
                screening_record(matched=True, results=[{"match_id": "m1"}]),
                has_pep_hit=True,
            )
        ],
    )
    add_screening_review(
        db, app_id, subject_name="Zara Late", requires_four_eyes=True,
        second_reviewer_id="officer-9",
    )
    findings = _findings_for(db, app_id)
    assert not [f for f in findings if f["category"] == "sanctions"]


def test_a_clear_subject_is_not_examined_for_disposition(db):
    """No match, nothing to disposition. The absence is not a finding."""
    app_id = seed_application(db)
    add_screening(db, app_id, company=CLEAN)
    findings = _findings_for(db, app_id)
    assert not [f for f in findings if f["category"] == "sanctions"]


# ── Check 4: freshness, evaluated against the injected as_of ─────────


def test_expired_screening_is_a_hit(db):
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        company=screening_record(valid_until="2026-06-30T00:00:00"),
        screening_valid_until="2026-06-30T00:00:00",
    )
    finding = next(f for f in _findings_for(db, app_id) if "expired" in f["claim"])
    assert finding["status"] == "hit"
    assert "2026-07-31" in finding["claim"]


def test_freshness_boundary_is_inclusive_of_the_expiry_instant(db):
    """Valid *through* the expiry instant; stale strictly after it.

    Both sides of the boundary are asserted, because an off-by-one here
    silently converts current screening into an expiry finding on every case
    that renews exactly on time.
    """
    on_boundary = seed_application(db)
    add_screening(db, on_boundary, company=screening_record(valid_until=AS_OF))
    assert "expired" not in claims(_findings_for(db, on_boundary))

    one_second_before = seed_application(db)
    add_screening(
        db, one_second_before,
        company=screening_record(valid_until="2026-07-30T23:59:59Z"),
    )
    assert "expired" in claims(_findings_for(db, one_second_before))


def test_as_of_is_the_only_time_source(db):
    """Moving the review instant, and only that, changes the freshness answer."""
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(valid_until=AS_OF))
    assert "expired" not in claims(
        run_probe(bundle_for(db, app_id, as_of="2026-07-30T00:00:00Z"), PROBE)
    )
    assert "expired" in claims(
        run_probe(bundle_for(db, app_id, as_of="2026-08-30T00:00:00Z"), PROBE)
    )


def test_absent_validity_date_is_unavailable_not_stale(db):
    app_id = seed_application(db)
    add_screening(
        db, app_id, company=screening_record(valid_until=None),
        screening_valid_until=None,
    )
    finding = next(
        f for f in _findings_for(db, app_id) if "no validity expiry" in f["claim"]
    )
    assert finding["status"] == "unavailable"


def test_unreadable_validity_date_is_never_treated_as_current(db):
    """A malformed date is a data defect, not a passing check."""
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(valid_until="soon"))
    finding = next(
        f for f in _findings_for(db, app_id) if "not a readable timestamp" in f["claim"]
    )
    assert finding["status"] == "unavailable"
    assert finding["status"] != "clear"


# ── Check 5: adverse media absence ───────────────────────────────────


def test_clear_adverse_media_score_with_no_screening_report(db):
    """The sharpest finding: absence represented as a clear result."""
    app_id = seed_application(
        db, risk_dimensions=risk_dimensions([factor("adverse_media", rule_score=1)])
    )
    findings = _findings_for(db, app_id)
    finding = next(f for f in findings if f["category"] == "adverse_media")
    assert "Absence of an assessment was represented as a clear result" in finding["claim"]
    assert finding["severity"] == "medium"


def test_adverse_media_finding_carries_the_coverage_note(db):
    from supervisor_foundation.probes._draft import ADVERSE_MEDIA_COVERAGE_NOTE

    app_id = seed_application(
        db, risk_dimensions=risk_dimensions([factor("adverse_media", rule_score=1)])
    )
    finding = next(
        f for f in _findings_for(db, app_id) if f["category"] == "adverse_media"
    )
    assert ADVERSE_MEDIA_COVERAGE_NOTE in finding["why_it_matters"]


def test_adverse_media_check_is_silent_when_a_report_exists(db):
    app_id = seed_application(
        db, risk_dimensions=risk_dimensions([factor("adverse_media", rule_score=1)])
    )
    add_screening(db, app_id, company=CLEAN)
    findings = _findings_for(db, app_id)
    assert not [f for f in findings if f["category"] == "adverse_media"]


def test_adverse_media_check_is_silent_when_the_factor_scored_above_clear(db):
    app_id = seed_application(
        db, risk_dimensions=risk_dimensions([factor("adverse_media", rule_score=3)])
    )
    findings = _findings_for(db, app_id)
    assert not [f for f in findings if f["category"] == "adverse_media"]


def test_adverse_media_check_runs_even_with_no_screening_subjects(db):
    """It is precisely about the case where no subject evidence exists."""
    app_id = seed_application(
        db, risk_dimensions=risk_dimensions([factor("adverse_media", rule_score=1)])
    )
    categories = {f["category"] for f in _findings_for(db, app_id)}
    assert "adverse_media" in categories
    assert "screening" in categories


# ── Availability ─────────────────────────────────────────────────────


def test_no_screening_evidence_is_unavailable_never_clear(db):
    app_id = seed_application(db)
    finding = only(_findings_for(db, app_id))
    assert finding["status"] == "unavailable"
    assert finding["availability_status"] == "data_absent"


# ── Clear ────────────────────────────────────────────────────────────


def test_fully_defensible_screening_records_an_explicit_clear(db):
    app_id = seed_application(db)
    add_screening(
        db, app_id, company=CLEAN,
        directors=[person_entry("Jane Doe", CLEAN)],
        ubos=[person_entry("John Roe", CLEAN)],
    )
    finding = only(_findings_for(db, app_id))
    assert finding["status"] == "clear"
    assert "All 3 screening subjects" in finding["claim"]


# ── Finding identity ─────────────────────────────────────────────────


def test_same_defect_on_two_subjects_yields_two_identities(db):
    """The collision this probe is most exposed to.

    Both findings cite the same application-level approval references, so
    without a per-subject identity anchor they would collapse into one.
    """
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        directors=[
            person_entry("Jane Doe", screening_record(api_status="sandbox")),
            person_entry("John Roe", screening_record(api_status="sandbox")),
        ],
    )
    findings = [
        f for f in _findings_for(db, app_id) if "sandbox provider" in f["claim"]
    ]
    assert len(findings) == 2
    assert len({f["finding_id"] for f in findings}) == 2


def test_four_defects_on_one_subject_yield_four_identities(db):
    """Provider, terminality, disposition and freshness on a single subject."""
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        directors=[
            person_entry(
                "Jane Doe",
                screening_record(
                    api_status="sandbox", matched=True,
                    results=[{"match_id": "m1"}],
                    valid_until="2026-01-01T00:00:00",
                ),
                has_sanctions_hit=True,
            )
        ],
        screening_valid_until="2026-01-01T00:00:00",
    )
    findings = _findings_for(db, app_id)
    assert len(findings) == 4
    assert len({f["finding_id"] for f in findings}) == 4
    assert len({f["primary_evidence_ref"] for f in findings}) == 4


def test_every_finding_identity_is_unique_across_a_busy_case(db):
    app_id = seed_application(
        db, risk_dimensions=risk_dimensions([factor("adverse_media", rule_score=1)])
    )
    add_screening(
        db, app_id,
        company=screening_record(api_status="pending"),
        directors=[
            person_entry("Jane Doe", screening_record(api_status="sandbox")),
            person_entry("John Roe", screening_record(valid_until="2020-01-01T00:00:00")),
        ],
        ubos=[person_entry("Zara Late", screening_record(api_status="error"))],
    )
    findings = _findings_for(db, app_id)
    assert len(findings) > 4
    assert len({f["finding_id"] for f in findings}) == len(findings)


def test_findings_are_stable_across_repeated_runs(db):
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        directors=[
            person_entry("Jane Doe", screening_record(api_status="sandbox")),
            person_entry("John Roe", screening_record(api_status="simulated")),
        ],
    )
    bundle = bundle_for(db, app_id)
    assert run_probe(bundle, PROBE) == run_probe(bundle, PROBE)


# ── Privacy ──────────────────────────────────────────────────────────


def test_findings_never_name_a_screened_person(db):
    """Subjects are addressed by pseudonymous key, not by name.

    The bundle is PII-minimised by design; a finding that printed 'Jane Doe'
    would reintroduce a direct identifier into an artifact meant to travel.
    """
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        directors=[
            person_entry(
                "Jane Doe",
                screening_record(api_status="sandbox", matched=True,
                                 results=[{"match_id": "m1"}]),
                has_pep_hit=True,
            )
        ],
    )
    findings = _findings_for(db, app_id)
    serialised = repr(findings)
    assert "Jane Doe" not in serialised
    assert "director_screening_0" in serialised


# ── Clock discipline ─────────────────────────────────────────────────


def test_probe_calls_no_clock_bearing_screening_helper():
    """The two wall-clock helpers are named in the module and never called."""
    import inspect

    source = inspect.getsource(screening_defensibility)
    body = source.split('"""', 2)[-1]
    assert "build_screening_truth_summary(" not in body
    assert "derive_screening_truth(" not in body
