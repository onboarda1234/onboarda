"""Phase 4 audit remediation: disposition-workflow fixtures + fixture gating.

Covers:
* the QA fixture seeder produces exactly the officer-facing queue states the
  disposition workflow validation needs (audit section D was blocked on this);
* fixture gating — seeded and text-pattern (PORTALE2E-style) rows are
  invisible in the default queue and visible only behind the opt-in;
* the application-scan cap is reported honestly in metrics;
* pending-second-review rows expose the first reviewer.
"""

import json

import pytest

import server
from seed_screening_qa_fixtures import (
    FIXTURE_REFS,
    seed_screening_qa_fixtures,
    wipe_screening_qa_fixtures,
)

OFFICER = {"type": "officer", "sub": "admin001"}


@pytest.fixture
def db(temp_db):
    """Guard-enforcing wrapped connection (overrides conftest's raw sqlite).

    The first staging seeder run was denied by the regulated-delete
    interceptor while the raw-sqlite test fixture had silently bypassed it.
    These tests must run through the same DB wrapper staging uses so the
    sanctioned-context path is what is actually exercised.
    """
    from db import get_db

    conn = get_db()
    yield conn
    conn.close()


def _rows_by_ref(db, *, show_fixtures):
    payload = server._build_screening_queue_payload(
        db,
        OFFICER,
        show_fixtures=show_fixtures,
        limit=100,
        offset=0,
        filters={"application_ref": "ARF-QAFIX-"},
        include_evidence=False,
    )
    rows = {}
    for row in payload["rows"]:
        rows.setdefault(row["application_ref"], []).append(row)
    return payload, rows


def test_seeder_produces_every_disposition_workflow_state(db):
    refs = seed_screening_qa_fixtures(db)
    assert list(refs) == list(FIXTURE_REFS)

    _, rows = _rows_by_ref(db, show_fixtures=True)
    assert set(rows) == set(FIXTURE_REFS)

    # 001 — unresolved sanctions hit: actionable review with the four quick actions.
    entity_rows = {r["subject_type"]: r for r in rows["ARF-QAFIX-001"]}
    open_hit = entity_rows["entity"]
    assert open_hit["canonical_status_key"] == "review_required"
    assert open_hit["review_required"] is True
    assert open_hit["total_hits"] == 1
    # Its director joins via person_key despite the provider-divergent stored
    # name ('nadia kovac' vs party 'Nadia A. KOVAC') — live-payload proof of
    # the Phase 2 subject-identity fix.
    director = entity_rows["director"]
    assert director["subject_name"] == "Nadia A. KOVAC"
    assert director["canonical_status_key"] == "clear"

    # 002 — four-eyes lock: pending second review, first reviewer exposed.
    locked = rows["ARF-QAFIX-002"][0]
    assert locked["review_four_eyes_status"] == "pending_second_review"
    assert locked["reviewed_by"] == "QA First Reviewer"
    assert locked["canonical_status_key"] == "review_required"

    # 003 — RFI recorded.
    follow_up = rows["ARF-QAFIX-003"][0]
    assert follow_up["canonical_status_key"] == "follow_up_required"
    assert follow_up["canonical_status"] == "Follow-up Required"

    # 004 — provider error.
    failed = rows["ARF-QAFIX-004"][0]
    assert failed["canonical_status_key"] == "failed"
    assert failed["canonical_status"] == "Failed / Provider Error"

    # 005 — stale screen must not read as clear.
    stale = rows["ARF-QAFIX-005"][0]
    assert stale["canonical_status_key"] == "stale"
    assert stale["canonical_status"] == "Stale / Requires Refresh"


def test_seeder_is_idempotent_and_wipe_removes_the_set(db):
    seed_screening_qa_fixtures(db)
    seed_screening_qa_fixtures(db)  # re-run must not duplicate
    _, rows = _rows_by_ref(db, show_fixtures=True)
    assert sum(len(items) for items in rows.values()) == 6  # 5 entities + 1 director

    wipe_screening_qa_fixtures(db)
    _, rows = _rows_by_ref(db, show_fixtures=True)
    assert rows == {}


def test_seeded_fixtures_hidden_from_default_queue(db):
    seed_screening_qa_fixtures(db)
    _, rows = _rows_by_ref(db, show_fixtures=False)
    assert rows == {}


def test_seeder_refuses_production(db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        seed_screening_qa_fixtures(db)
    with pytest.raises(RuntimeError):
        wipe_screening_qa_fixtures(db)


def test_text_pattern_fixture_rows_hidden_from_default_queue(db):
    """Audit finding: PORTALE2E/E2E-style rows (predating reliable is_fixture
    marking) appeared in the default queue because the queue skipped the
    text-pattern arm of the fixture policy used by the other surfaces."""
    ref = "PORTALE2E-QAGATE-000001"
    db.execute("DELETE FROM applications WHERE ref = ?", (ref,))
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, is_fixture)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "qagate0000000001", ref, "testclient001",
            "PORTALE2E-QAGATE Approval Clean Ltd", "Mauritius", "Technology",
            "SME", "in_review",
            json.dumps({"screening_report": {
                "provider": "complyadvantage",
                "screened_at": "2026-07-01T00:00:00Z",
                "screening_mode": "live",
                "company_screening": {
                    "provider": "complyadvantage", "source": "complyadvantage",
                    "api_status": "live", "matched": False, "results": [],
                    "sanctions": {"source": "complyadvantage", "api_status": "live", "matched": False, "results": []},
                },
                "director_screenings": [], "ubo_screenings": [], "intermediary_screenings": [],
                "overall_flags": [], "total_hits": 0,
            }}), False,
        ),
    )
    db.commit()

    def refs(show_fixtures):
        payload = server._build_screening_queue_payload(
            db, OFFICER, show_fixtures=show_fixtures, limit=100, offset=0,
            filters={"application_ref": "PORTALE2E-QAGATE-"}, include_evidence=False,
        )
        return {row["application_ref"] for row in payload["rows"]}

    assert ref not in refs(False)
    assert ref in refs(True)


def test_application_scan_cap_reported_in_metrics(db):
    seed_screening_qa_fixtures(db)
    payload, _ = _rows_by_ref(db, show_fixtures=True)
    metrics = payload["metrics"]
    assert metrics["application_scan_cap"] == server._SCREENING_QUEUE_APPLICATION_SCAN_CAP
    assert isinstance(metrics["applications_scanned"], int)
    assert metrics["application_scan_capped"] == (
        metrics["applications_scanned"] >= metrics["application_scan_cap"]
    )


def test_seeder_context_satisfies_the_regulated_delete_guard():
    """Fidelity check for the staging denial (RegulatedDeleteDenied on
    screening_reviews). The SQL-level interceptor short-circuits on verified
    isolated test databases, so exercise the guard function directly: without
    a context it denies exactly as staging did; inside the seeder's
    fixture_cleanup_nonprod context it allows screening_reviews and ONLY
    screening_reviews."""
    from regulated_deletion import RegulatedDeleteDenied, assert_regulated_delete_allowed
    from seed_screening_qa_fixtures import _fixture_cleanup_context

    with pytest.raises(RegulatedDeleteDenied):
        assert_regulated_delete_allowed("screening_reviews")

    with _fixture_cleanup_context("fidelity test"):
        assert_regulated_delete_allowed("screening_reviews")  # sanctioned
        with pytest.raises(RegulatedDeleteDenied):
            assert_regulated_delete_allowed("audit_log")  # outside scope


def test_seeder_uses_python_booleans_for_postgres_boolean_columns():
    """Staging regression (psycopg2 DatatypeMismatch): is_fixture / is_pep /
    requires_four_eyes are BOOLEAN on PostgreSQL. Integer literals that SQLite
    tolerates are rejected by psycopg2; Python bools adapt on both dialects."""
    from seed_screening_qa_fixtures import FIXTURES
    import inspect
    import seed_screening_qa_fixtures as seeder_module

    for fixture in FIXTURES:
        for director in fixture["directors"]:
            assert isinstance(director["is_pep"], bool)
        if fixture["review"]:
            assert isinstance(fixture["review"]["requires_four_eyes"], bool)
    # The applications INSERT must bind is_fixture as a Python bool literal.
    source = inspect.getsource(seeder_module.seed_screening_qa_fixtures)
    assert "True," in source


def test_seeder_satisfies_every_foreign_key_and_client_is_login_proof(db):
    """Staging regression #3 (psycopg2 ForeignKeyViolation on
    applications_client_id_fkey): sqlite does not enforce foreign keys by
    default, so referential integrity is asserted here by explicit query —
    dialect-independent. The owning client must exist, be inactive (never a
    usable login), and be removed again by the wipe."""
    from seed_screening_qa_fixtures import QAFIX_CLIENT_ID

    seed_screening_qa_fixtures(db)

    client = db.execute(
        "SELECT id, status, company_name FROM clients WHERE id = ?", (QAFIX_CLIENT_ID,)
    ).fetchone()
    assert client is not None
    assert client["status"] == "inactive"

    orphaned_apps = db.execute(
        "SELECT ref FROM applications WHERE ref LIKE 'ARF-QAFIX-%' AND client_id NOT IN (SELECT id FROM clients)"
    ).fetchall()
    assert [dict(r) for r in orphaned_apps] == []
    orphaned_directors = db.execute(
        "SELECT id FROM directors WHERE application_id LIKE 'f1xedqa%' AND application_id NOT IN (SELECT id FROM applications)"
    ).fetchall()
    assert [dict(r) for r in orphaned_directors] == []
    orphaned_reviews = db.execute(
        "SELECT id FROM screening_reviews WHERE application_id LIKE 'f1xedqa%' AND application_id NOT IN (SELECT id FROM applications)"
    ).fetchall()
    assert [dict(r) for r in orphaned_reviews] == []

    wipe_screening_qa_fixtures(db)
    assert db.execute(
        "SELECT id FROM clients WHERE id = ?", (QAFIX_CLIENT_ID,)
    ).fetchone() is None
