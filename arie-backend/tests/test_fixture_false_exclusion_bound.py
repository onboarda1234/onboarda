"""Pilot audit — fixture text patterns must never hide a REAL applicant.

The fixture text patterns (``%e2e%``, ``%smoke%``, ``%fixture%``, ``%audit-%``,
``arf-2026-9000%`` …) are substring matches on applicant-controlled text
(``ref`` / ``company_name``). They exist only to catch historical smoke/E2E
rows created before ``is_fixture`` was reliably set. Applied unbounded they
also silently drop genuine applicants — "Smoke House Trading Ltd", "Fixture
Supplies Ltd", "Audit-Pro Advisory" — from every officer's default queue.

A real screened subject vanishing from the compliance queue is a worse failure
than a fixture leaking in, so the heuristic arm is now bounded to rows created
before ``FIXTURE_TEXT_PATTERN_CUTOFF``. The authoritative markers (``f1xed%``
id namespace, ``is_fixture`` column) still match at any age, so genuinely
seeded fixtures remain excluded.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from fixture_filter import (
    FIXTURE_TEXT_PATTERN_CUTOFF,
    fixture_app_exclude_clause,
)

BEFORE_CUTOFF = "2026-01-05 09:00:00"
AFTER_CUTOFF = "2026-12-01 09:00:00"


@pytest.fixture
def apps(temp_db):
    from db import get_db

    conn = get_db()
    yield conn
    conn.close()


def _insert(conn, *, app_id, ref, name, created_at, is_fixture=0):
    conn.execute(
        "INSERT INTO applications (id, ref, company_name, status, is_fixture, created_at) "
        "VALUES (?, ?, ?, 'submitted', ?, ?)",
        (app_id, ref, name, is_fixture, created_at),
    )
    conn.commit()


def _visible_refs(conn):
    excl, params = fixture_app_exclude_clause(table_alias="", include_text_patterns=True)
    rows = conn.execute(
        f"SELECT ref FROM applications WHERE {excl} ORDER BY ref", params
    ).fetchall()
    return [r["ref"] for r in rows]


# --- the defect: real applicants were being hidden ---------------------------

@pytest.mark.parametrize(
    "ref,name",
    [
        ("ARF-2026-500001", "Smoke House Trading Ltd"),
        ("ARF-2026-500002", "Fixture Supplies Ltd"),
        ("ARF-2026-500003", "Audit-Pro Advisory Ltd"),
        ("ARF-2026-500004", "E2E Logistics Ltd"),
        ("ARF-2026-90001", "Genuine Applicant Ltd"),          # ref-block pattern
    ],
)
def test_real_applicant_created_after_cutoff_is_visible(apps, ref, name):
    _insert(apps, app_id="real-" + ref, ref=ref, name=name, created_at=AFTER_CUTOFF)
    assert ref in _visible_refs(apps), (
        f"real applicant {name!r} ({ref}) was hidden from the officer queue"
    )


# --- behaviour preservation: historical rows still excluded ------------------

@pytest.mark.parametrize(
    "ref,name",
    [
        ("PORTALE2E-001", "Portal E2E Probe"),
        ("ARF-2026-90002", "Historic Smoke Row"),
        ("ARF-SMOKE-77", "Browser-Smoke Check"),
    ],
)
def test_historical_text_pattern_rows_stay_excluded(apps, ref, name):
    _insert(apps, app_id="hist-" + ref, ref=ref, name=name, created_at=BEFORE_CUTOFF)
    assert ref not in _visible_refs(apps)


def test_null_created_at_is_treated_as_historical(apps):
    # Fail-closed for fixtures of unknown vintage.
    _insert(apps, app_id="null-1", ref="PORTALE2E-NULL", name="E2E Probe", created_at=None)
    assert "PORTALE2E-NULL" not in _visible_refs(apps)


# --- authoritative markers are age-independent -------------------------------

def test_is_fixture_marked_row_excluded_regardless_of_age(apps):
    _insert(
        apps,
        app_id="marked-1",
        ref="ARF-2026-700001",
        name="Perfectly Normal Ltd",
        created_at=AFTER_CUTOFF,
        is_fixture=1,
    )
    assert "ARF-2026-700001" not in _visible_refs(apps)


def test_f1xed_namespace_excluded_regardless_of_age(apps):
    _insert(
        apps,
        app_id="f1xed-newrow",
        ref="ARF-QAFIX-900",
        name="Perfectly Normal Ltd",
        created_at=AFTER_CUTOFF,
    )
    assert "ARF-QAFIX-900" not in _visible_refs(apps)


# --- the bound itself --------------------------------------------------------

def test_cutoff_is_pinned_and_parameterised():
    # Moving this date forward re-opens the hole for every application
    # onboarded in the interim — it is pinned deliberately.
    assert FIXTURE_TEXT_PATTERN_CUTOFF == "2026-07-27 00:00:00"
    _, params = fixture_app_exclude_clause(table_alias="", include_text_patterns=True)
    assert FIXTURE_TEXT_PATTERN_CUTOFF in params


def test_cutoff_not_applied_when_text_patterns_are_off():
    sql, params = fixture_app_exclude_clause(table_alias="", include_text_patterns=False)
    assert "created_at" not in sql
    assert FIXTURE_TEXT_PATTERN_CUTOFF not in params


def test_alias_form_qualifies_the_created_at_column():
    sql, _ = fixture_app_exclude_clause(table_alias="a", include_text_patterns=True)
    assert "a.created_at" in sql
