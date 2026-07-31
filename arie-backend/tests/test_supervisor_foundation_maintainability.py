"""Guards for the coupling and drift risks found in the closure-pass review.

The assembler is a flat file of independent section builders, which is why the
maintainability review concluded "keep as-is". Three couplings survive that
review, and each fails *silently* rather than loudly if a future edit breaks it.
These tests turn each into a loud failure.

None of these tests changes assembler output. They are pure guards.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import supervisor_foundation.bundle as bundle_module
from supervisor_foundation import assemble_application_bundle
from supervisor_foundation.bundle import APPLICATION_FIELDS, ORDERING_RULES
from supervisor_foundation_fixtures import AS_OF, complete_standard

BUNDLE_SOURCE = pathlib.Path(bundle_module.__file__)


# ── Risk 1: availability reads three other sections by string key ────
# `_availability_section` decides dependency states from keys owned by the
# screening, risk and memo builders. Renaming one of those keys upstream would
# not raise — it would silently flip a dependency to unavailable, which is the
# worst possible failure mode for a module whose whole job is to distinguish
# "did not run" from "passed".

#: (section, key) pairs `_availability_section` depends on.
AVAILABILITY_CONTRACT = (
    ("screening", "report_present"),
    ("risk", "evidence_present"),
    ("memo", "present"),
)


@pytest.mark.parametrize("section,key", AVAILABILITY_CONTRACT)
def test_availability_source_keys_exist(db, section, key):
    """Each key `_availability_section` reads is actually produced upstream."""
    bundle = assemble_application_bundle(db, complete_standard(db), as_of=AS_OF)
    assert key in bundle[section], (
        f"_availability_section reads {section}.{key}, which no longer exists; "
        "the dependency state would silently degrade"
    )


def test_availability_contract_is_complete(db):
    """The pairs listed above are the only cross-section reads.

    If a new cross-section dependency is added without extending
    ``AVAILABILITY_CONTRACT``, this fails and the guard stays honest.
    """
    source = BUNDLE_SOURCE.read_text(encoding="utf-8")
    body = source[
        source.index("def _availability_section") : source.index(
            "def assemble_application_bundle"
        )
    ]
    found = set(re.findall(r"(screening|risk|memo)\.get\(\"(\w+)\"\)", body))
    assert found == set(AVAILABILITY_CONTRACT), (
        f"cross-section reads changed: {sorted(found)}; update "
        "AVAILABILITY_CONTRACT so the guard still covers every dependency"
    )


# ── Risk 2: ORDERING_RULES is prose that can drift from the real sorts ──
# The documented rule and the inline sort key are two separate statements of the
# same fact. This checks the documented key actually orders the real collection.

#: Documented path -> (accessor, sort-key fields). Only paths whose key is a
#: plain field tuple can be verified mechanically; the rest are listed in
#: ``UNVERIFIABLE_ORDERINGS`` with a reason so nothing is silently skipped.
VERIFIABLE_ORDERINGS = {
    "parties.directors": (lambda b: b["parties"]["directors"]["rows"],),
    "parties.ubos": (lambda b: b["parties"]["ubos"]["rows"],),
    "parties.intermediaries": (lambda b: b["parties"]["intermediaries"]["rows"],),
    "documents.active": (lambda b: b["documents"]["active"],),
    "document_gate.expectations": (lambda b: b["document_gate"]["expectations"],),
    "screening.reviews": (lambda b: b["screening"]["reviews"],),
    "edd.cases": (lambda b: b["edd"]["cases"],),
    "decision.records": (lambda b: b["decision"]["records"],),
    "monitoring.periodic_reviews": (lambda b: b["monitoring"]["periodic_reviews"],),
    "risk.factors": (lambda b: b["risk"]["factors"],),
    "risk.dimensions": (lambda b: b["risk"]["dimensions"],),
}

UNVERIFIABLE_ORDERINGS = {
    "documents.active[].checks": "nested per-document; covered by its own test",
    "memo.section_keys": "a list of strings, not of records",
    "*.absent_fields": "wildcard path; covered by its own recursive test",
}


def _multi_row_case(db) -> str:
    """``complete_standard`` plus a second row in every verifiable collection.

    Local to this file so the shared fixture corpus — and therefore every
    existing bundle hash — is untouched. A one-row collection is trivially
    "sorted", so without this the ordering guard would skip most paths and
    prove almost nothing.
    """
    import json
    import uuid

    suffix = uuid.uuid4().hex[:8]
    application_id = complete_standard(db)
    ref = db.execute(
        "SELECT ref FROM applications WHERE id = ?", (application_id,)
    ).fetchone()["ref"]

    db.execute(
        "INSERT INTO directors (id, application_id, person_key, full_name, nationality) "
        "VALUES (?,?,?,?,?)",
        (f"dirB_{suffix}", application_id, "p-director-0", "Aaron Early", "MU"),
    )
    db.execute(
        "INSERT INTO ubos (id, application_id, person_key, full_name, nationality, "
        "ownership_pct) VALUES (?,?,?,?,?,?)",
        (f"uboB_{suffix}", application_id, "p-ubo-0", "Aaron Early", "MU", 12.0),
    )
    db.execute(
        "INSERT INTO intermediaries (id, application_id, person_key, entity_name, "
        "jurisdiction, ownership_pct) VALUES (?,?,?,?,?,?)",
        (f"intB_{suffix}", application_id, "p-inter-0", "Second Holdco", "MT", 10.0),
    )
    db.execute(
        "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, "
        "slot_key, verification_status, is_current) VALUES (?,?,?,?,?,?,?,?)",
        (f"docB_{suffix}", application_id, "board_res", "Board Resolution", "/tmp/y.pdf",
         "board_res", "verified", 1),
    )
    db.execute(
        "INSERT INTO screening_reviews (application_id, subject_type, subject_name, "
        "disposition) VALUES (?,?,?,?)",
        (application_id, "company", "Aaron Early", "cleared"),
    )
    db.execute(
        "INSERT INTO edd_cases (application_id, client_name, risk_level, stage, "
        "trigger_source) VALUES (?,?,?,?,?)",
        (application_id, "Fixture Holdings Ltd", "HIGH", "triggered", "officer_decision"),
    )
    db.execute(
        "INSERT INTO decision_records (id, application_ref, decision_type, source, "
        "actor_user_id, actor_role, timestamp) VALUES (?,?,?,?,?,?,?)",
        (f"decB_{suffix}", ref, "pre_approve", "manual", "officer-3", "co", "2026-07-10T09:00:00"),
    )
    db.execute(
        "INSERT INTO periodic_reviews (application_id, client_name, risk_level, "
        "next_review_date, trigger_type, status) VALUES (?,?,?,?,?,?)",
        (application_id, "Fixture Holdings Ltd", "HIGH", "2027-07-20", "adhoc", "pending"),
    )
    # A second risk dimension so risk.dimensions has something to order.
    blob = json.loads(
        db.execute(
            "SELECT risk_dimensions FROM applications WHERE id = ?", (application_id,)
        ).fetchone()["risk_dimensions"]
    )
    blob["factor_computation_evidence"]["dimensions"].insert(
        0,
        {
            "dimension_id": "D2",
            "dimension_score": 1.5,
            "dimension_weight": 25.0,
            "composite_contribution": 4.0,
            "factor_keys": ["country_of_incorporation"],
        },
    )
    db.execute(
        "UPDATE applications SET risk_dimensions = ? WHERE id = ?",
        (json.dumps(blob), application_id),
    )
    db.commit()
    return application_id


@pytest.mark.parametrize("path", sorted(VERIFIABLE_ORDERINGS))
def test_documented_ordering_rule_matches_the_actual_order(db, path):
    """Each collection is really sorted by the key ``ORDERING_RULES`` claims."""
    bundle = assemble_application_bundle(db, _multi_row_case(db), as_of=AS_OF)
    accessor = VERIFIABLE_ORDERINGS[path][0]
    rows = accessor(bundle)
    assert len(rows) >= 2, (
        f"{path} has fewer than two rows, so ordering is untested; extend "
        "_multi_row_case"
    )

    fields = [field.strip() for field in ORDERING_RULES[path].strip("()").split(",")]
    keys = [tuple(str(row.get(field) or "") for field in fields) for row in rows]
    assert keys == sorted(keys), (
        f"{path} is not ordered by its documented key {ORDERING_RULES[path]}"
    )


def test_every_ordering_rule_is_either_verified_or_explained():
    """No documented rule is silently unchecked."""
    covered = set(VERIFIABLE_ORDERINGS) | set(UNVERIFIABLE_ORDERINGS)
    assert set(ORDERING_RULES) == covered, (
        "ORDERING_RULES changed; add the new path to VERIFIABLE_ORDERINGS or "
        "record why it cannot be checked in UNVERIFIABLE_ORDERINGS"
    )


# ── Risk 3: fields duplicated across sections must not diverge ───────
# Five columns are projected into two sections each. That is redundancy rather
# than a defect — both reads come from the same row — but if a future edit
# transforms one copy and not the other, the bundle would carry two different
# answers to the same question. Removing the duplication would change the
# bundle hash, so it is deferred to a schema version bump; this pins the values
# equal in the meantime.

DUPLICATED_FIELDS = (
    ("risk", "base_risk_level"),
    ("risk", "final_risk_level"),
    ("risk", "elevation_reason_text"),
    ("decision", "decided_at"),
    ("decision", "decision_by"),
)


@pytest.mark.parametrize("section,field", DUPLICATED_FIELDS)
def test_duplicated_fields_agree_across_sections(db, section, field):
    """A field carried in two sections must hold the same value in both."""
    bundle = assemble_application_bundle(db, complete_standard(db), as_of=AS_OF)
    assert field in APPLICATION_FIELDS, f"{field} is no longer duplicated; drop it here"
    assert bundle["application"]["fields"][field] == bundle[section][field], (
        f"{field} diverged between application and {section}"
    )


# ── SQL portability ──────────────────────────────────────────────────


def test_all_sql_uses_portable_placeholders_and_no_dialect_functions():
    """Statements must run unchanged on SQLite and, after translation, on PostgreSQL.

    ``DBConnection`` rewrites ``?`` to ``%s`` for PostgreSQL, so ``?`` is the
    portable choice. Dialect-specific date functions would survive translation
    and break — and would also be a clock.
    """
    source = BUNDLE_SOURCE.read_text(encoding="utf-8")
    statements = re.findall(r'"(SELECT [^"]*)"', source) + re.findall(
        r'f"(SELECT [^"]*)"', source
    )
    assert statements, "no SELECT statements found — the scan is not working"

    for statement in statements:
        assert "%s" not in statement, f"non-portable placeholder in: {statement}"
        for forbidden in ("datetime('now')", "date('now')", "NOW()", "CURRENT_DATE"):
            assert forbidden not in statement, (
                f"dialect-specific clock function in SQL: {statement}"
            )


def test_interpolated_table_names_come_from_literals_only():
    """The one f-string SELECT interpolates a table name, so pin its safety."""
    tree = ast.parse(BUNDLE_SOURCE.read_text(encoding="utf-8"))
    # Only f-strings that *are* a statement, not ones that merely mention the
    # word (the non-SELECT tripwire's error message does).
    joined = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        and node.values
        and isinstance(node.values[0], ast.Constant)
        and str(node.values[0].value).lstrip().upper().startswith("SELECT")
    ]
    assert len(joined) == 1, "a new interpolated SQL string appeared; re-audit it"

    # Its only call site passes a literal table name.
    source = BUNDLE_SOURCE.read_text(encoding="utf-8")
    call_sites = re.findall(r'collect\("(\w+)", \w+\)', source)
    assert sorted(call_sites) == ["directors", "intermediaries", "ubos"]


# ── No speculative abstraction ───────────────────────────────────────


def test_assembler_defines_no_classes():
    """The assembler is functions and constants — no framework, no hierarchy."""
    tree = ast.parse(BUNDLE_SOURCE.read_text(encoding="utf-8"))
    classes = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name != "BundleAssemblyError"
    ]
    assert not classes, f"unexpected classes in the assembler: {classes}"


def test_only_the_application_subject_type_is_implemented():
    """Reserved subject types stay reserved until their phase."""
    from supervisor_foundation.bundle import BundleAssemblyError  # noqa: F401
    from supervisor_foundation.contracts import SubjectType

    source = BUNDLE_SOURCE.read_text(encoding="utf-8")
    for member in SubjectType:
        if member is SubjectType.APPLICATION:
            continue
        assert f"SubjectType.{member.name}" not in source, (
            f"{member.name} is reserved for a later phase but is referenced now"
        )
