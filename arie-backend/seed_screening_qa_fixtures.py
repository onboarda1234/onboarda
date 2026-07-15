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
===============  =============================  ==================================

Usage::

    python3 seed_screening_qa_fixtures.py          # seed/refresh the set
    python3 seed_screening_qa_fixtures.py --wipe   # remove the set

The seeder is idempotent (each run deletes and re-inserts the fixed refs) and
refuses to run when ``ENVIRONMENT`` is ``production``.
"""

import json
import os
import sys

SCREENED_AT = "2026-07-01T00:00:00Z"
QAFIX_CLIENT_ID = "qafix-client"
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
                "is_pep": 0,
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
            "requires_four_eyes": 1,
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
            "requires_four_eyes": 0,
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
]

FIXTURE_REFS = tuple(f["ref"] for f in FIXTURES)


def _guard_environment():
    environment = str(os.environ.get("ENVIRONMENT") or "").strip().lower()
    if environment == "production":
        raise RuntimeError(
            "seed_screening_qa_fixtures refuses to run with ENVIRONMENT=production"
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


def wipe_screening_qa_fixtures(db):
    """Remove the QA fixture set (idempotent)."""
    _guard_environment()
    fixture_ids = [f["id"] for f in FIXTURES]
    placeholders = ",".join("?" for _ in fixture_ids)
    with _fixture_cleanup_context("Remove/refresh screening QA disposition fixtures (f1xedqa namespace)"):
        db.execute(f"DELETE FROM screening_reviews WHERE application_id IN ({placeholders})", fixture_ids)
    db.execute(f"DELETE FROM directors WHERE application_id IN ({placeholders})", fixture_ids)
    db.execute(f"DELETE FROM applications WHERE id IN ({placeholders})", fixture_ids)
    db.commit()
    return len(fixture_ids)


def seed_screening_qa_fixtures(db):
    """Seed (or refresh) the QA fixture set. Returns the seeded refs."""
    _guard_environment()
    wipe_screening_qa_fixtures(db)
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
                json.dumps({
                    "company_name": fixture["company"],
                    "screening_report": fixture["report"],
                    "last_screened_at": SCREENED_AT,
                }),
                1,
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
