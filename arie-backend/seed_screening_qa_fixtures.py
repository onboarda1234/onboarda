"""
seed_screening_qa_fixtures.py — Disposition-workflow QA fixtures.
=================================================================

Seeds a deterministic set of ``is_fixture`` applications covering every
officer-facing screening-queue state, so the disposition workflow (including
the four-eyes second-review rule) can be exercised end-to-end on staging
without touching real applications. This closes audit finding D ("only QA hit
fixture was already locked pending second review — no accepted disposition
could be safely submitted").

Fixture set (reserved ``f1xed`` id namespace + ``is_fixture = 1``; company
names deliberately contain "QA Fixture" so the text-pattern arm of the
fixture policy also catches them):

===============  =============================  ==================================
Ref              Company                        Queue state exercised
===============  =============================  ==================================
ARF-QAFIX-001    QA Fixture Open Hit Ltd        Review Required (unresolved sanctions
                                                hit, all four actions available);
                                                also carries a director whose
                                                stored entry joins via person_key
                                                with a provider-divergent name.
ARF-QAFIX-002    QA Fixture Second Review Ltd   Pending second review (first
                                                officer cleared as false positive,
                                                four-eyes lock active).
ARF-QAFIX-003    QA Fixture Follow Up Ltd       Follow-up Required (RFI recorded).
ARF-QAFIX-004    QA Fixture Provider Error Ltd  Failed / Provider Error.
ARF-QAFIX-005    QA Fixture Stale Ltd           Stale / Requires Refresh.
ARF-QAFIX-008    QA Fixture Licensed Entity Ltd Clear; declares a regulatory
                                                licence and carries an
                                                entity:licence document so the
                                                LIC-GATE PASS path is
                                                exercisable (006/007 are
                                                occupied staging identities —
                                                see the FIXTURES entry).
===============  =============================  ==================================

Usage::

    python3 seed_screening_qa_fixtures.py          # seed/refresh the set
    python3 seed_screening_qa_fixtures.py --wipe   # remove the set

The seeder is idempotent (each run deletes and re-inserts the fixed refs) and
runs only in the environments listed in :data:`SEEDER_ALLOWED_ENVIRONMENTS`
(testing / test / staging) — anything else, including an unset ``ENVIRONMENT``,
is refused.
"""

import json
import os
import sys

SCREENED_AT = "2026-07-01T00:00:00Z"
QAFIX_CLIENT_ID = "qafix-client"
QAFIX_CLIENT_EMAIL = "screening-qa-fixtures@fixtures.invalid"
FIRST_REVIEWER_NAME = "QA First Reviewer"

_HIT_RESULT = {
    "name": "QA Watchlist Subject",
    "matching_name": "QA Watchlist Subject",
    "is_sanctioned": True,
    "match_categories": ["sanctions"],
    "categories": ["sanctions"],
    "provider_risk_identifier": "qafix-risk-0001",
    "provider_profile_identifier": "qafix-profile-0001",
    "provider_alert_identifier": "qafix-alert-0001",
}


def _company_screening(*, matched, api_status="live", source="complyadvantage", valid_until=None):
    sanctions = {
        "source": source,
        "api_status": api_status,
        "screened_at": SCREENED_AT,
        "matched": matched,
        "results": [dict(_HIT_RESULT)] if matched else [],
    }
    if valid_until:
        sanctions["screening_valid_until"] = valid_until
    return {
        "provider": "complyadvantage",
        "source": source,
        "api_status": api_status,
        "screened_at": SCREENED_AT,
        "matched": matched,
        "results": [dict(_HIT_RESULT)] if matched else [],
        "sanctions": sanctions,
        "adverse_media": {
            "source": source,
            "api_status": api_status,
            "screened_at": SCREENED_AT,
            "matched": False,
            "results": [],
        },
    }


def _report(*, matched, api_status="live", valid_until=None, director_entries=None):
    company = _company_screening(matched=matched, api_status=api_status, valid_until=valid_until)
    terminal = api_status == "live" and not valid_until
    return {
        "provider": "complyadvantage",
        "screened_at": SCREENED_AT,
        "screening_mode": "live" if api_status == "live" else "unavailable",
        "company_screening_coverage": "full",
        "has_company_screening_hit": bool(matched) if terminal else None,
        "company_screening_state": (
            "completed_match" if (terminal and matched) else ("completed_clear" if terminal else "failed")
        ),
        "company_screening": company,
        "director_screenings": list(director_entries or []),
        "ubo_screenings": [],
        "intermediary_screenings": [],
        "overall_flags": (["Company has sanctions/watchlist matches"] if matched else []),
        "total_hits": 1 if matched else 0,
        "degraded_sources": [] if api_status == "live" else ["company_watchlist"],
        "any_non_terminal_subject": False,
    }


# Director entry stored under a provider-divergent name; joins to the party
# below only via person_key (exercises the Phase 2 subject-identity fix on
# a live payload).
_QAFIX_DIRECTOR_ENTRY = {
    "person_name": "nadia kovac",
    "subject_name": "Nadia A. KOVAC",
    "person_key": "f1xed-dir-0001",
    "person_type": "director",
    "nationality": "HR",
    "declared_pep": "No",
    "provider_detected_pep": False,
    "undeclared_pep": False,
    "has_pep_hit": False,
    "has_sanctions_hit": False,
    "has_adverse_media_hit": None,
    "screening": {
        "provider": "complyadvantage",
        "source": "complyadvantage",
        "api_status": "live",
        "screened_at": SCREENED_AT,
        "matched": False,
        "results": [],
    },
    "screening_state": "completed_clear",
}

FIXTURES = [
    {
        "id": "f1xedqa000000001",
        "ref": "ARF-QAFIX-001",
        "company": "QA Fixture Open Hit Ltd",
        "report": _report(matched=True, director_entries=[_QAFIX_DIRECTOR_ENTRY]),
        "directors": [
            {
                "id": "f1xedqa0dir00001",
                "person_key": "f1xed-dir-0001",
                "full_name": "Nadia A. KOVAC",
                "nationality": "HR",
                "is_pep": False,
            }
        ],
        "review": None,
    },
    {
        "id": "f1xedqa000000002",
        "ref": "ARF-QAFIX-002",
        "company": "QA Fixture Second Review Ltd",
        "report": _report(matched=True),
        "directors": [],
        "review": {
            "disposition": "cleared",
            "disposition_code": "false_positive_cleared",
            "rationale": "QA fixture: first-officer false-positive clearance awaiting a second reviewer.",
            "sensitivity_flags": json.dumps(["director_ubo_sensitive_hit"]),
            "requires_four_eyes": True,
            "reviewer_name": FIRST_REVIEWER_NAME,
        },
    },
    {
        "id": "f1xedqa000000003",
        "ref": "ARF-QAFIX-003",
        "company": "QA Fixture Follow Up Ltd",
        "report": _report(matched=True),
        "directors": [],
        "review": {
            "disposition": "follow_up_required",
            "disposition_code": "needs_more_information",
            "rationale": "QA fixture: additional client information requested.",
            "sensitivity_flags": json.dumps([]),
            "requires_four_eyes": False,
            "reviewer_name": FIRST_REVIEWER_NAME,
        },
    },
    {
        "id": "f1xedqa000000004",
        "ref": "ARF-QAFIX-004",
        "company": "QA Fixture Provider Error Ltd",
        "report": _report(matched=False, api_status="error"),
        "directors": [],
        "review": None,
    },
    {
        "id": "f1xedqa000000005",
        "ref": "ARF-QAFIX-005",
        "company": "QA Fixture Stale Ltd",
        "report": _report(matched=False, valid_until="2026-01-01T00:00:00Z"),
        "directors": [],
        "review": None,
    },
    {
        # PR-C rider: the only fixture whose pre-screening DECLARES a
        # regulatory licence, with a licence document seeded so the
        # LIC-GATE PASS path is exercisable end-to-end (every other
        # fixture declares no licence, so the gate always short-circuits
        # to SKIP). The fixture:// file_path follows the convention of
        # every other seeder — evidence checks degrade on the virtual
        # file, which is fine: the gate itself is pure prescreening logic.
        #
        # Identity is deliberately 008: ARF-QAFIX-006 and -007 are OCCUPIED
        # staging identities outside this seeder's managed set — 006 is the
        # protected-module pending-second-review evidence specimen pinned by
        # the PR-MON-PROTECTED-BASELINE-1 semantic baseline and both staging
        # smoke scripts. Reusing an occupied id would hijack it and the wipe
        # would destroy the specimen.
        "id": "f1xedqa000000008",
        "ref": "ARF-QAFIX-008",
        "company": "QA Fixture Licensed Entity Ltd",
        "report": _report(matched=False),
        "directors": [],
        "review": None,
        "prescreening_extra": {
            "regulatory_licences": "EMI licence Malta, ref QA-12345",
            "registered_entity_name": "QA Fixture Licensed Entity Ltd",
            "business_activity": "Electronic money issuance",
        },
        "documents": [
            {
                "id": "f1xedqa0000doc08",
                "doc_type": "licence",
                "doc_name": "QA Regulatory Licence.pdf",
                "file_path": "fixture://qa-licence-008",
                "slot_key": "entity:licence",
            }
        ],
    },
]

FIXTURE_REFS = tuple(f["ref"] for f in FIXTURES)


# Fail-closed allow-list of environments this seeder may write to.
#
# The previous guard denied only the literal string ``"production"``: an unset,
# empty, misspelt or simply unrecognised ``ENVIRONMENT`` ("prod", "PRODUCTION "
# with stray whitespace, or a production database whose env var was never
# wired) passed this check. In practice such a run still failed closed, because
# :func:`seed_screening_qa_fixtures` wipes before it inserts and the wipe enters
# the ``fixture_cleanup_nonprod`` regulated-delete context, which refuses any
# environment outside testing/test/staging — so no SQL executed. This guard is
# therefore defense in depth and legibility, not the removal of a reachable
# production-write path: it fails at the seeder's own boundary, with an
# actionable message, instead of surfacing an opaque ValueError from the
# deletion layer.
#
# The set MUST stay a subset of the ``fixture_cleanup_nonprod`` policy in
# ``regulated_deletion._validate_context``. Advertising an environment that the
# deletion layer then rejects sends operators down a dead end, and would leave
# the INSERT path declaring a wider policy than anything actually enforces it.
# ``test_seeder_environment_allowlist.py`` pins that subset relationship.
#
# ACCEPTED RESIDUAL RISK — ``staging`` is also the pilot stack
# (staging.regmind.co, per CLAUDE.md), so a single unconfirmed CLI run can
# write fixtures into a pilot database. Blast radius is bounded (five
# hard-coded ``f1xed*`` ids plus the qafix client, all ``is_fixture=True`` and
# therefore hidden from the default officer queue; blast radius is bounded to
# the hard-coded ``f1xed*`` ids in FIXTURES plus the qafix client — deleted
# only when no out-of-set application still references it), but the runbook
# records effects the wipe cannot undo: fixture-linked ``edd_cases`` are not
# cleared, verification runs against the ARF-QAFIX-008 licence document leave
# orphan ``agent_executions`` rows (no FK), and re-seeding reuses application
# ids, which conflicts on the live ComplyAdvantage Mesh account. ``arie-backend/fixtures/cli.py`` already
# implements the stronger pattern (ENVIRONMENT=staging AND ALLOW_FIXTURE_SEED=1
# AND an explicit --confirm token); adopting it here is the tracked follow-up.
# It is deliberately NOT changed in this commit because it alters the operator
# contract documented in the screening operations runbook.
SEEDER_ALLOWED_ENVIRONMENTS = frozenset({"testing", "test", "staging"})


def _guard_environment():
    environment = str(os.environ.get("ENVIRONMENT") or "").strip().lower()
    if environment not in SEEDER_ALLOWED_ENVIRONMENTS:
        raise RuntimeError(
            "seed_screening_qa_fixtures refuses to run with ENVIRONMENT="
            + (environment or "(unset)")
            + " — seeding is allowed only in: "
            + ", ".join(sorted(SEEDER_ALLOWED_ENVIRONMENTS))
        )


def _fixture_cleanup_context(reason):
    """Sanctioned deletion context for the fixture reseed.

    ``screening_reviews`` is a regulated table: the DB layer denies raw
    DELETEs without an approved context (staging enforced this with
    RegulatedDeleteDenied when the first seeder ran raw SQL). The
    ``fixture_cleanup_nonprod`` context is the approved channel — it is only
    valid in testing/staging environments, requires an explicit fixture
    marker + confirmation, and scopes the permission to exactly the one
    regulated table this seeder touches.
    """
    from regulated_deletion import sanctioned_delete_context

    return sanctioned_delete_context(
        "fixture_cleanup_nonprod",
        actor_id="seed_screening_qa_fixtures",
        role="system",
        reason=reason,
        allowed_tables=("screening_reviews",),
        environment=os.environ.get("ENVIRONMENT"),
        is_fixture=True,
        confirmed=True,
    )


def _ensure_qafix_client(db):
    """Create the disabled owning client for the fixture applications.

    ``applications.client_id`` is a foreign key to ``clients`` on PostgreSQL
    (the isolated sqlite test database does not enforce it — the third
    staging-only seeder failure was exactly this ForeignKeyViolation). The
    client row is login-proof: ``inactive`` status plus a bcrypt hash of a
    discarded random secret that is never stored anywhere.
    """
    existing = db.execute(
        "SELECT id FROM clients WHERE id = ?", (QAFIX_CLIENT_ID,)
    ).fetchone()
    if existing:
        return
    import bcrypt

    unusable_hash = bcrypt.hashpw(
        os.urandom(24).hex().encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, ?, ?, 'inactive')",
        (QAFIX_CLIENT_ID, QAFIX_CLIENT_EMAIL, unusable_hash, "QA Fixture Client (disabled)"),
    )


def wipe_screening_qa_fixtures(db):
    """Remove the QA fixture set, including its owning client (idempotent)."""
    _guard_environment()
    fixture_ids = [f["id"] for f in FIXTURES]
    placeholders = ",".join("?" for _ in fixture_ids)
    with _fixture_cleanup_context("Remove/refresh screening QA disposition fixtures (f1xedqa namespace)"):
        db.execute(f"DELETE FROM screening_reviews WHERE application_id IN ({placeholders})", fixture_ids)
    # documents is not a regulated table (the sanctioned-context validator
    # rejects unclassified tables in its scope) — plain delete like directors.
    db.execute(f"DELETE FROM documents WHERE application_id IN ({placeholders})", fixture_ids)
    db.execute(f"DELETE FROM directors WHERE application_id IN ({placeholders})", fixture_ids)
    db.execute(f"DELETE FROM applications WHERE id IN ({placeholders})", fixture_ids)
    # Delete the owning client only when nothing else references it: on
    # staging, applications OUTSIDE this seeder's managed set (e.g. the
    # ARF-QAFIX-006 protected-module specimen) share qafix-client, and
    # applications.client_id has no ON DELETE action — an unconditional
    # delete would FK-fail and abort the reseed.
    remaining = db.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE client_id = ?",
        (QAFIX_CLIENT_ID,),
    ).fetchone()
    if not int((remaining or {}).get("c") or 0):
        db.execute("DELETE FROM clients WHERE id = ?", (QAFIX_CLIENT_ID,))
    db.commit()
    return len(fixture_ids)


def seed_screening_qa_fixtures(db):
    """Seed (or refresh) the QA fixture set. Returns the seeded refs."""
    _guard_environment()
    wipe_screening_qa_fixtures(db)
    _ensure_qafix_client(db)
    for fixture in FIXTURES:
        db.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type,
             status, prescreening_data, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixture["id"],
                fixture["ref"],
                QAFIX_CLIENT_ID,
                fixture["company"],
                "Mauritius",
                "Technology",
                "SME",
                "in_review",
                json.dumps(dict(
                    {
                        "company_name": fixture["company"],
                        "screening_report": fixture["report"],
                        "last_screened_at": SCREENED_AT,
                    },
                    **fixture.get("prescreening_extra", {})
                )),
                # Python bool, NOT an integer literal: is_fixture is BOOLEAN on
                # PostgreSQL (psycopg2 raises DatatypeMismatch for ints, which
                # SQLite silently tolerated — the first staging seeder run
                # failed exactly here).
                True,
            ),
        )
        for director in fixture["directors"]:
            db.execute(
                """
                INSERT INTO directors
                (id, application_id, person_key, full_name, nationality, is_pep)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    director["id"],
                    fixture["id"],
                    director["person_key"],
                    director["full_name"],
                    director["nationality"],
                    director["is_pep"],
                ),
            )
        for document in fixture.get("documents", []):
            db.execute(
                """
                INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, slot_key,
                 verification_status, is_current, version)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 1)
                """,
                (
                    document["id"],
                    fixture["id"],
                    document["doc_type"],
                    document["doc_name"],
                    document["file_path"],
                    # slot_key must match "<category>:<doc_type>" or the back
                    # office integrity gate rejects the row
                    # (conflicting_document_slot_identity).
                    document["slot_key"],
                    # Python bool for the same PostgreSQL BOOLEAN reason as
                    # is_fixture (documents.is_current is BOOLEAN on PG).
                    True,
                ),
            )
        review = fixture["review"]
        if review:
            db.execute(
                """
                INSERT INTO screening_reviews
                (application_id, subject_type, subject_name, disposition, notes,
                 disposition_code, rationale, sensitivity_flags, requires_four_eyes,
                 reviewer_id, reviewer_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fixture["id"],
                    "entity",
                    fixture["company"],
                    review["disposition"],
                    review["rationale"],
                    review["disposition_code"],
                    review["rationale"],
                    review["sensitivity_flags"],
                    review["requires_four_eyes"],
                    None,
                    review["reviewer_name"],
                ),
            )
    db.commit()
    return list(FIXTURE_REFS)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from db import get_db

    db = get_db()
    try:
        if "--wipe" in argv:
            count = wipe_screening_qa_fixtures(db)
            print(f"Removed {count} screening QA fixtures")
        else:
            refs = seed_screening_qa_fixtures(db)
            print(f"Seeded {len(refs)} screening QA fixtures: {', '.join(refs)}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
