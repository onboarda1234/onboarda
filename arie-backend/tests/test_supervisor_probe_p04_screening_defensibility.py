"""P-04 — screening reliance defensibility.

Four checks: provider defensibility, match disposition and freshness per
subject, plus adverse-media absence once per application.

This suite carries the identity-collision regression for the whole probe set.
P-04 is the only probe that fires several times on one subject *and* once per
subject across many, so if finding identity is fragile it breaks here first. It
also carries the entailment proof that justifies there being four checks rather
than five — see ``test_terminality_is_entailed_by_provider_mode``.
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


def test_non_terminal_state_is_named_in_the_provider_finding(db):
    """One finding per subject, carrying both the mode and the derived state."""
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record(api_status="pending"))
    finding = only(_findings_for(db, app_id))
    assert "'pending'" in finding["claim"]
    assert "pending_provider" in finding["claim"]


def test_terminal_states_match_the_authoritative_set():
    """The probe's terminal set is the platform's, not a private opinion."""
    from screening_state import TERMINAL_STATES as AUTHORITATIVE

    assert screening_defensibility.TERMINAL_STATES == frozenset(AUTHORITATIVE)


@pytest.mark.parametrize(
    "api_status", ["live", "sandbox", "simulated", "pending", "error",
                   "not_configured", ""]
)
@pytest.mark.parametrize("matched", [True, False])
def test_terminality_is_entailed_by_provider_mode(api_status, matched):
    """Why this probe has no separate terminality check.

    Over the whole provider-mode vocabulary, a live mode always derives a
    terminal state and every other mode always derives a non-terminal one. A
    terminality finding could therefore only ever repeat the provider finding.
    If ``screening_state`` ever allows the two to diverge, this fails and the
    removed check needs reinstating.
    """
    from screening_state import derive_screening_state, provider_mode_from_record

    record = {
        "provider": "complyadvantage",
        "api_status": api_status,
        "matched": matched,
        "results": [{"match_id": "m1"}] if matched else [],
    }
    mode = provider_mode_from_record(record)
    state = derive_screening_state(record)
    is_terminal = state in screening_defensibility.TERMINAL_STATES
    assert is_terminal == (mode == screening_defensibility.LIVE_PROVIDER_MODE), (
        f"api_status={api_status!r} matched={matched}: mode={mode!r} "
        f"state={state!r} — provider mode and terminality have diverged"
    )


def test_unavailable_provider_yields_one_finding_per_subject_not_two(db):
    """The noise regression.

    An unconfigured provider used to produce a provider finding *and* a
    terminality finding for every subject. On the production-path corpus that
    was 33 findings across 8 cases, half of them redundant.
    """
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        company=screening_record(api_status="not_configured"),
        directors=[
            person_entry("Jane Doe", screening_record(api_status="not_configured")),
            person_entry("John Roe", screening_record(api_status="not_configured")),
        ],
    )
    approve(db, app_id)
    findings = _findings_for(db, app_id)
    provider_findings = [
        f for f in findings if "Screening provider state" in f["claim"]
    ]
    assert len(provider_findings) == 3
    assert all(f["availability_status"] == "credentials_absent"
               for f in provider_findings)


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


def test_two_defects_on_one_subject_yield_two_identities(db):
    """Provider and disposition on a single subject.

    Freshness is suppressed here on purpose: the provider answer is a sandbox
    result, so "is it still current?" has no useful answer. Disposition still
    fires — an unassessed match survives a re-screen.
    """
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
    assert len(findings) == 2
    assert {f["category"] for f in findings} == {"screening", "sanctions"}
    assert len({f["finding_id"] for f in findings}) == 2
    assert len({f["primary_evidence_ref"] for f in findings}) == 2
    assert "expired" not in claims(findings)


def test_freshness_still_runs_when_the_provider_answer_is_defensible(db):
    """The suppression must not swallow a stale live result."""
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        directors=[
            person_entry(
                "Jane Doe",
                screening_record(valid_until="2026-01-01T00:00:00"),
            )
        ],
        screening_valid_until="2026-01-01T00:00:00",
    )
    finding = only(_findings_for(db, app_id))
    assert "expired" in finding["claim"]


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
    assert len(findings) >= 4
    assert len({f["finding_id"] for f in findings}) == len(findings)
    assert len({f["primary_evidence_ref"] for f in findings}) == len(findings)


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


# ── Fail-closed: unreadable review instant ───────────────────────────
# An earlier build skipped freshness when `as_of` would not parse and then
# reported subjects "within their validity period" — asserting the very thing
# it had not checked.


def test_unreadable_as_of_never_produces_clear(db):
    app_id = seed_application(db)
    add_screening(
        db, app_id,
        company=screening_record(valid_until="2020-01-01T00:00:00"),
        screening_valid_until="2020-01-01T00:00:00",
    )
    bundle = bundle_for(db, app_id)
    bundle = {**bundle, "meta": {**bundle["meta"], "as_of": "not-a-timestamp"}}
    findings = run_probe(bundle, PROBE)

    assert all(f["status"] != "clear" for f in findings)
    assert "within their validity period" not in claims(findings)

    unreadable = next(f for f in findings if "not a readable timestamp" in f["claim"])
    assert unreadable["status"] == "unavailable"
    assert unreadable["availability_status"] == "data_absent"


@pytest.mark.parametrize("bad_as_of", ["", None, "not-a-timestamp", "2026-13-45"])
def test_every_unreadable_as_of_shape_fails_closed(db, bad_as_of):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record())
    bundle = bundle_for(db, app_id)
    bundle = {**bundle, "meta": {**bundle["meta"], "as_of": bad_as_of}}
    findings = run_probe(bundle, PROBE)
    assert all(f["status"] != "clear" for f in findings)


def test_a_readable_as_of_still_reaches_clear(db):
    """The fail-closed path must not swallow the healthy case."""
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record())
    assert only(_findings_for(db, app_id))["status"] == "clear"


# ── Fail-closed: unknown provider mode ───────────────────────────────


@pytest.mark.parametrize("mode", [
    "quantum_provider",          # a value screening_state does not produce today
    "live provider",             # right words, wrong separator
    "  ",                        # whitespace only
    "",                          # empty
    None,                        # null
])
def test_unknown_provider_mode_is_never_defensible(db, mode):
    """Only the exact live-provider mode may reach `clear`.

    ``provider_mode_from_record`` returns six known values and falls back to
    ``pending``, so these are unreachable through the authoritative helper — but
    the default has to be closed. A mode added upstream tomorrow must not walk
    a subject to `clear` because the probe did not recognise it.
    """
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record())
    bundle = bundle_for(db, app_id)
    subject = dict(bundle["screening"]["subjects"][0])
    subject["provider_mode"] = mode
    bundle = {**bundle, "screening": {**bundle["screening"], "subjects": [subject]}}

    findings = run_probe(bundle, PROBE)
    assert all(f["status"] != "clear" for f in findings)
    provider = next(f for f in findings if "Screening provider state" in f["claim"])
    assert provider["status"] == "unavailable"


def test_provider_mode_case_and_padding_are_normalised_not_rejected(db):
    """Leniency where leniency is right.

    A stored value differing only in case or surrounding whitespace is the same
    mode. Failing closed on that would report a defensible live screening as
    unestablished — a false positive dressed as caution.
    """
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record())
    bundle = bundle_for(db, app_id)
    subject = dict(bundle["screening"]["subjects"][0], provider_mode="  LIVE_PROVIDER ")
    bundle = {**bundle, "screening": {**bundle["screening"], "subjects": [subject]}}
    assert only(run_probe(bundle, PROBE))["status"] == "clear"


def test_an_unrecognised_mode_says_it_is_unrecognised(db):
    app_id = seed_application(db)
    add_screening(db, app_id, company=screening_record())
    bundle = bundle_for(db, app_id)
    subject = dict(bundle["screening"]["subjects"][0], provider_mode="quantum_provider")
    bundle = {**bundle, "screening": {**bundle["screening"], "subjects": [subject]}}
    claim = only(run_probe(bundle, PROBE))["claim"]
    assert "not a provider mode this platform is known to record" in claim


def test_only_live_provider_reaches_the_defensible_path():
    """Asserted against the authoritative vocabulary, not a private list."""
    from screening_state import (
        FAILED, LIVE_PROVIDER, NOT_CONFIGURED, PENDING, SANDBOX_PROVIDER,
        SIMULATED_FALLBACK,
    )

    every_mode = {
        LIVE_PROVIDER, SANDBOX_PROVIDER, SIMULATED_FALLBACK, PENDING,
        NOT_CONFIGURED, FAILED,
    }
    handled = (
        set(screening_defensibility.UNDEFENSIBLE_MODES)
        | set(screening_defensibility.UNAVAILABLE_MODES)
        | {screening_defensibility.LIVE_PROVIDER_MODE}
    )
    assert every_mode <= handled, f"unhandled provider modes: {every_mode - handled}"
    assert screening_defensibility.LIVE_PROVIDER_MODE == LIVE_PROVIDER


# ── Disposition integrity ────────────────────────────────────────────


def _matched_director(db, app_id, **review):
    add_screening(
        db, app_id,
        directors=[
            person_entry(
                "Zara Late",
                screening_record(matched=True, results=[{"match_id": "m1"}]),
                has_pep_hit=True,
            )
        ],
    )
    if review:
        add_screening_review(db, app_id, subject_name="Zara Late", **review)


def test_disposition_vocabulary_matches_the_governed_codes():
    """Mirrored from ``server``, asserted against it, never imported by the probe."""
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[1] / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    governed = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_SCREENING_DISPOSITION_CODES" for t in node.targets
        ):
            governed = {
                item.value
                for bucket in node.value.values
                for item in bucket.elts
                if isinstance(item, ast.Constant)
            }
            break

    assert governed, "could not locate _SCREENING_DISPOSITION_CODES in server.py"
    assert screening_defensibility.GOVERNED_DISPOSITION_CODES == frozenset(governed), (
        "the probe's disposition vocabulary has drifted from server.py"
    )


@pytest.mark.parametrize("code", ["false_positive", "confirmed_match",
                                  "needs_more_information"])
def test_a_governed_disposition_closes_the_match(db, code):
    app_id = seed_application(db)
    _matched_director(db, app_id, disposition_code=code)
    assert not [f for f in _findings_for(db, app_id) if f["category"] == "sanctions"]


@pytest.mark.parametrize("code,expected_phrase", [
    ("", "records no disposition code"),
    ("   ", "records no disposition code"),
    (None, "records no disposition code"),
    ("looks_fine_to_me", "not one of the governed screening dispositions"),
])
def test_a_malformed_disposition_does_not_close_the_match(db, code, expected_phrase):
    """A review row is not a disposition.

    ``screening_reviews.disposition_code`` is nullable and unconstrained, so a
    row can exist carrying nothing. Its mere presence must not close a material
    match.
    """
    app_id = seed_application(db)
    _matched_director(db, app_id, disposition_code=code)
    finding = next(
        f for f in _findings_for(db, app_id) if f["category"] == "sanctions"
    )
    assert finding["status"] == "hit"
    assert expected_phrase in finding["claim"]


def test_disposition_case_and_padding_are_normalised_not_rejected(db):
    """Same leniency, same reason: a padded governed code is a governed code."""
    app_id = seed_application(db)
    _matched_director(db, app_id, disposition_code="  FALSE_POSITIVE ")
    assert not [f for f in _findings_for(db, app_id) if f["category"] == "sanctions"]


def test_a_malformed_disposition_on_an_approved_case_is_critical(db):
    app_id = seed_application(db)
    _matched_director(db, app_id, disposition_code="")
    approve(db, app_id)
    finding = next(
        f for f in _findings_for(db, app_id) if f["category"] == "sanctions"
    )
    assert finding["severity"] == "critical"
    assert "approved on this review" in finding["claim"]


def test_four_eyes_is_still_enforced_on_a_governed_disposition(db):
    """The new validation must run before, not instead of, the sign-off check."""
    app_id = seed_application(db)
    _matched_director(db, app_id, disposition_code="false_positive",
                      requires_four_eyes=True, second_reviewer_id=None)
    finding = next(
        f for f in _findings_for(db, app_id) if f["category"] == "sanctions"
    )
    assert "no second reviewer is recorded" in finding["claim"]
