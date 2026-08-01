"""Cross-cutting guarantees for the Phase 0B-2 probe set.

Per-probe suites prove each probe answers its own question. This file proves the
properties that must hold for *every* probe, now and for every probe added
later: purity, one-way imports, honest availability, falsifiable language,
unique identity, and reproducibility.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path

import pytest

from supervisor_foundation import assemble_application_bundle, canonical_bytes, run_review
from supervisor_foundation.contracts import (
    PROBE_SET_VERSION,
    REQUIRED_FINDING_FIELDS,
    AvailabilityStatus,
    FindingStatus,
)
from supervisor_foundation.policy import unconfigured_policy
from supervisor_foundation.probes import PROBE_IDS, PROBE_VERSIONS, PROBES
from supervisor_foundation.probes._draft import BANNED_PHRASES
from supervisor_probe_fixtures import (
    AS_OF,
    add_memo,
    add_periodic_review,
    add_screening,
    add_screening_review,
    approve,
    factor,
    person_entry,
    risk_dimensions,
    routing_facts,
    screening_record,
    seed_application,
)

PROBE_PACKAGE = Path(inspect.getfile(__import__(
    "supervisor_foundation.probes", fromlist=["__file__"]
))).parent

PROBE_MODULES = sorted(
    path for path in PROBE_PACKAGE.glob("*.py") if path.name != "__init__.py"
)

#: Severities a probe may emit, mirroring the framework vocabulary.
SEVERITIES = {"critical", "high", "medium", "low", "info"}


# ── Fixture: one case that trips every probe at once ─────────────────


@pytest.fixture
def defective_case(db):
    """An approved application carrying at least one defect per probe.

    Deliberately messy. A finding set assembled from four probes over one
    subject is where cross-probe interference — duplicate identifiers, ordering
    instability, contradictory claims — would first appear.
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
                factor("country_of_incorporation", rule_score=4,
                       resolution_status="unresolved"),
                factor("adverse_media", rule_score=1),
            ]
        ),
    )
    add_memo(
        db,
        app_id,
        facts=routing_facts(
            final_risk_level="HIGH",
            declared_pep_present=True,
            jurisdiction_risk_tier="high",
            edd_trigger_flags=["declared_pep_present"],
        ),
        stored_route="edd",
    )
    add_screening(
        db,
        app_id,
        company=screening_record(api_status="sandbox"),
        directors=[
            person_entry(
                "Zara Late",
                screening_record(matched=True, results=[{"match_id": "m1"}],
                                 valid_until="2026-01-01T00:00:00"),
                has_pep_hit=True,
            )
        ],
    )
    approve(db, app_id, risk_level="HIGH")
    add_periodic_review(db, app_id, next_review_date="whenever", policy_version=None)
    return app_id


@pytest.fixture
def clean_case(db):
    """An approved application that trips nothing."""
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="standard")
    add_screening(
        db, app_id,
        company=screening_record(),
        directors=[person_entry("Jane Doe", screening_record())],
    )
    add_screening_review(db, app_id, subject_name="Jane Doe")
    approve(db, app_id)
    add_periodic_review(db, app_id, next_review_date="2027-06-01")
    return app_id


def _review(db, app_id, *, as_of=AS_OF):
    bundle = assemble_application_bundle(db, app_id, as_of=as_of)
    return run_review(bundle, policy=unconfigured_policy(), probes=PROBES)


# ── Registration ─────────────────────────────────────────────────────


def test_probe_set_is_the_four_authorised_probes():
    assert PROBE_IDS == ("P-02", "P-03", "P-04", "P-06")
    assert len(PROBES) == 4


def test_probe_set_version_names_this_set():
    """A stored review must not be reinterpretable under a different set."""
    assert PROBE_SET_VERSION == "probe-set-0b2-v1"
    assert PROBE_SET_VERSION != "probe-set-0b2-empty"


def test_each_module_declares_a_distinct_probe_version():
    assert len(set(PROBE_VERSIONS.values())) == len(PROBE_VERSIONS)


def test_findings_carry_the_declared_probe_versions(defective_case, db):
    review = _review(db, defective_case)
    for finding in review["findings"]:
        assert finding["probe_version"] == PROBE_VERSIONS[finding["probe_id"]]


# ── Contract compliance over real output ─────────────────────────────


def test_every_finding_satisfies_the_full_contract(defective_case, db):
    review = _review(db, defective_case)
    assert review["findings"]
    for finding in review["findings"]:
        for field in REQUIRED_FINDING_FIELDS:
            assert field in finding, field
            assert finding[field] not in (None, "", []), field
        assert finding["severity"] in SEVERITIES
        assert finding["status"] in {member.value for member in FindingStatus}
        assert finding["availability_status"] in {
            member.value for member in AvailabilityStatus
        }
        assert 0.0 <= finding["confidence"] <= 1.0
        assert finding["finding_id"]
        assert finding["created_at"] == AS_OF
        assert finding["primary_evidence_ref"] in finding["evidence_refs"]


def test_every_finding_answers_the_five_framework_questions(defective_case, db):
    """Claim, why, question, action, close condition — all substantive.

    A one-word ``required_action`` would satisfy the presence check above and
    be useless to an officer, so length is asserted where the field is not the
    deliberate "None." of a clear finding.
    """
    for finding in _review(db, defective_case)["findings"]:
        if finding["status"] in {"clear", "not_applicable"}:
            continue
        assert len(finding["claim"]) > 40
        assert len(finding["why_it_matters"]) > 40
        assert len(finding["officer_question"]) > 15
        assert len(finding["required_action"]) > 15
        assert len(finding["close_condition"]) > 15


def test_no_finding_uses_unfalsifiable_language(defective_case, clean_case, db):
    for app_id in (defective_case, clean_case):
        for finding in _review(db, app_id)["findings"]:
            text = " ".join(
                str(finding[field])
                for field in (
                    "claim", "why_it_matters", "officer_question",
                    "required_action", "close_condition",
                )
            ).lower()
            for phrase in BANNED_PHRASES:
                assert phrase not in text, f"{finding['probe_id']}: {phrase}"


def test_no_finding_declares_a_customer_compliant_or_not(defective_case, clean_case, db):
    """The Supervisor reports defects in evidence, never a verdict on a person."""
    for app_id in (defective_case, clean_case):
        for finding in _review(db, app_id)["findings"]:
            text = str(finding["claim"]).lower()
            for phrase in ("is compliant", "non-compliant", "is not compliant",
                           "money launderer", "is a criminal"):
                assert phrase not in text


# ── Availability honesty ─────────────────────────────────────────────


def test_clear_is_never_paired_with_a_non_available_status(defective_case, clean_case, db):
    for app_id in (defective_case, clean_case):
        for finding in _review(db, app_id)["findings"]:
            if finding["status"] == "clear":
                assert finding["availability_status"] == "available"


def test_unevaluable_checks_are_counted_separately(db):
    """A bare application: what could not run is visible, not absorbed.

    Three of the four probes have nothing to read. The one that does — risk
    provenance — evaluates and clears. The completeness block must show that
    split rather than reporting "no hits".
    """
    app_id = seed_application(db)
    review = _review(db, app_id)
    statuses = {f["probe_id"]: f["status"] for f in review["findings"]}
    assert statuses == {
        "P-02": "clear",
        "P-03": "unavailable",
        "P-04": "unavailable",
        "P-06": "not_applicable",
    }
    # ``not_applicable`` is not counted as unevaluable: the check ran and
    # correctly established that no obligation exists. Only 'could not run'
    # states — unavailable and not_replayable — inflate that count.
    assert review["review_completeness"]["unevaluable_count"] == 2
    assert review["review_completeness"]["probes_run"] == 4


def test_a_clean_case_produces_four_clears_not_silence(clean_case, db):
    review = _review(db, clean_case)
    statuses = {f["probe_id"]: f["status"] for f in review["findings"]}
    assert statuses == {
        "P-02": "clear", "P-03": "clear", "P-04": "clear", "P-06": "clear"
    }
    assert review["review_completeness"]["unevaluable_count"] == 0


# ── Identity and ordering ────────────────────────────────────────────


def test_finding_identifiers_are_unique_across_the_whole_set(defective_case, db):
    findings = _review(db, defective_case)["findings"]
    assert len(findings) >= 6
    assert len({f["finding_id"] for f in findings}) == len(findings)


def test_findings_are_ordered_canonically(defective_case, db):
    findings = _review(db, defective_case)["findings"]
    keys = [
        (f["probe_id"], f["category"], f["claim"], f["finding_id"])
        for f in findings
    ]
    assert keys == sorted(keys)


# ── Reproducibility ──────────────────────────────────────────────────


def test_review_is_byte_identical_across_repeated_runs(defective_case, db):
    reviews = [_review(db, defective_case) for _ in range(3)]
    assert len({canonical_bytes(review) for review in reviews}) == 1
    assert len({review["review_hash"] for review in reviews}) == 1


def test_probe_order_does_not_affect_the_review_hash(defective_case, db):
    bundle = assemble_application_bundle(db, defective_case, as_of=AS_OF)
    forward = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
    reverse = run_review(
        bundle, policy=unconfigured_policy(), probes=tuple(reversed(PROBES))
    )
    assert forward["review_hash"] == reverse["review_hash"]


def test_review_hash_changes_when_the_probe_set_version_changes(defective_case, db):
    bundle = assemble_application_bundle(db, defective_case, as_of=AS_OF)
    baseline = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
    relabelled = run_review(
        bundle, policy=unconfigured_policy(), probes=PROBES,
        probe_set_version="probe-set-hypothetical",
    )
    assert baseline["review_hash"] != relabelled["review_hash"]


# ── Purity: no clock ─────────────────────────────────────────────────


def test_no_probe_module_reaches_a_wall_clock():
    """Asserted over the AST so prose about clocks does not weaken the check."""
    forbidden_calls = {"now", "utcnow", "today", "time", "monotonic"}
    for path in PROBE_MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(
                func, "id", ""
            )
            assert name not in forbidden_calls, f"{path.name} calls {name}()"


def test_review_is_identical_under_two_different_wall_clocks(defective_case, db):
    """The strongest form of the no-clock claim: behaviour, not source text.

    Two assemblies separated by a real wall-clock advance must be byte-identical
    when ``as_of`` is unchanged.
    """
    import time

    first = _review(db, defective_case)
    time.sleep(1.1)
    second = _review(db, defective_case)
    assert canonical_bytes(first) == canonical_bytes(second)


def test_moving_as_of_is_the_only_way_to_move_a_time_dependent_finding(
    defective_case, db
):
    baseline = _review(db, defective_case, as_of=AS_OF)
    shifted = _review(db, defective_case, as_of="2027-12-31T00:00:00Z")
    assert baseline["review_hash"] != shifted["review_hash"]


# ── Purity: no writes ────────────────────────────────────────────────


def test_running_every_probe_writes_nothing(defective_case, db):
    def snapshot():
        rows = {}
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall():
            rows[name] = db.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        return rows

    before = snapshot()
    _review(db, defective_case)
    assert snapshot() == before


def test_probes_cannot_write_through_a_read_only_connection(defective_case, db, tmp_path):
    """Belt and braces: the review runs against a connection that would raise."""
    source = db.execute("PRAGMA database_list").fetchall()[0]["file"]
    uri = f"file:{source}?mode=ro"
    read_only = sqlite3.connect(uri, uri=True)
    read_only.row_factory = sqlite3.Row
    try:
        bundle = assemble_application_bundle(read_only, defective_case, as_of=AS_OF)
        review = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
        assert review["findings"]
    finally:
        read_only.close()


# ── Import boundary ──────────────────────────────────────────────────


def test_no_authoritative_module_imports_the_probe_package():
    """One-way dependency: the Supervisor observes, it is never depended upon.

    An authoritative module importing a probe would make an advisory finding
    capable of changing a workflow outcome, which is precisely the boundary
    Phase 0A drew.
    """
    backend = PROBE_PACKAGE.parent.parent
    offenders = []
    for path in backend.rglob("*.py"):
        if "supervisor_foundation" in path.parts or "tests" in path.parts:
            continue
        if any(part in {"archive", "scripts", ".venv", "node_modules"}
               for part in path.parts):
            continue
        text = path.read_text(errors="ignore")
        if "supervisor_foundation.probes" in text or "from .probes" in text:
            offenders.append(str(path.relative_to(backend)))
    assert not offenders, f"authoritative modules import the probe package: {offenders}"


def test_probe_modules_import_no_provider_or_network_client():
    forbidden = {
        "requests", "httpx", "urllib", "socket", "boto3",
        "sumsub_client", "complyadvantage", "screening_client", "claude_client",
        "anthropic",
    }
    for path in PROBE_MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in forbidden, f"{path.name} imports {name}"


def test_probe_modules_touch_no_database_api():
    """No probe calls anything that could reach a connection.

    Asserted over identifiers rather than SQL keywords: the modules discuss
    what they must not do ("never create, update or complete an EDD case"), and
    a keyword scan would flag the documentation instead of the behaviour.
    """
    forbidden = {"execute", "executemany", "commit", "rollback", "cursor",
                 "fetchone", "fetchall", "get_db"}
    for path in PROBE_MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(
                func, "id", ""
            )
            assert name not in forbidden, f"{path.name} calls {name}()"


def test_probes_accept_no_database_handle():
    for probe in PROBES:
        parameters = list(inspect.signature(probe).parameters)
        assert parameters == ["bundle", "policy"]


# ── Bundle-key drift ─────────────────────────────────────────────────
# Probes read the bundle by string key. A renamed key upstream would not raise:
# it would silently flip a check to "unavailable", which is the worst failure
# mode for code whose job is telling "did not run" apart from "passed". Each key
# below is asserted to exist on a fully-populated bundle.

#: ``(section, key)`` pairs the probe set depends on. Extend this when a probe
#: starts reading a new key, or the guard stops being honest.
BUNDLE_KEY_CONTRACT = (
    ("risk", "evidence_present"),
    ("risk", "factors"),
    ("risk", "factor_count"),
    ("risk", "risk_config_version"),
    ("risk", "final_risk_level"),
    ("risk", "base_risk_level"),
    ("risk", "escalations"),
    ("risk", "elevation_reason_text"),
    ("edd", "routing_facts"),
    ("edd", "stored_routing"),
    ("edd", "cases"),
    ("screening", "subjects"),
    ("screening", "reviews"),
    ("screening", "report_present"),
    ("monitoring", "periodic_reviews"),
    ("decision", "records"),
    ("decision", "decided_at"),
    ("application", "fields"),
    ("meta", "as_of"),
    ("meta", "subject_id"),
)


@pytest.mark.parametrize("section,key", BUNDLE_KEY_CONTRACT)
def test_bundle_keys_the_probes_depend_on_exist(defective_case, db, section, key):
    bundle = assemble_application_bundle(db, defective_case, as_of=AS_OF)
    assert key in bundle[section], (
        f"a probe reads {section}.{key}, which the assembler no longer produces; "
        "the check would silently degrade to unavailable"
    )


def test_nested_keys_the_probes_depend_on_exist(defective_case, db):
    bundle = assemble_application_bundle(db, defective_case, as_of=AS_OF)

    routing_facts_section = bundle["edd"]["routing_facts"]
    for key in ("present", "facts", "absent_fact_keys"):
        assert key in routing_facts_section, key

    subject = bundle["screening"]["subjects"][0]
    for key in ("provider_mode", "screening_state", "has_material_hit",
                "review_id", "screening_valid_until", "subject_key"):
        assert key in subject, key

    review = bundle["monitoring"]["periodic_reviews"][0]
    for key in ("id", "next_review_date", "last_review_date", "policy_version"):
        assert key in review, key


def test_probes_read_no_section_outside_the_bundle_contract():
    """A probe reaching for a section the bundle does not define is a bug.

    Catches the mirror of the rename risk: a probe inventing a key that never
    existed reads as ``None`` and degrades silently in exactly the same way.
    """
    from supervisor_foundation.contracts import BUNDLE_SECTIONS

    known = set(BUNDLE_SECTIONS)
    for path in PROBE_MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # Matches ``bundle.get("section")`` only — nested lookups are
            # covered by the fixture-backed tests above.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "bundle"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                assert node.args[0].value in known, (
                    f"{path.name} reads unknown bundle section "
                    f"{node.args[0].value!r}"
                )


# ── Policy contract ──────────────────────────────────────────────────


def test_probes_run_against_a_fully_unconfigured_policy(defective_case, db):
    """No probe may depend on institution policy that Phase 0B does not manage.

    These four are policy-independent by design; if one later needs a policy
    key, it must report ``policy_not_configured`` rather than assume a default.
    """
    review = _review(db, defective_case)
    assert review["findings"]
    assert review["policy_version"] == unconfigured_policy().policy_version
