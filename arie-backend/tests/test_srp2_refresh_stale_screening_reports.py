"""SRP-2: governed refresh of stale (pre-enrichment) screening reports.

Covers the governance rails the founder approved:
* selection targets only blind, hit-bearing, non-fixture reports;
* applications with officer adjudications are skipped, never batch-refreshed;
* the outgoing report is archived BEFORE replacement and survives a failed
  re-screen (archive-first ordering);
* dry-run mutates nothing; production is refused;
* the archive table is protected by the regulated-deletion guard;
* every refresh writes a hash-chained audit entry.
"""

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ops"))

from refresh_stale_screening_reports import (
    ARCHIVE_ACTOR,
    classify_report,
    refresh_stale_screening_reports,
    select_candidates,
)


@pytest.fixture
def db(temp_db):
    """Wrapper-enforcing connection (archive inserts + chained audit writes
    must run through the same DBConnection staging uses)."""
    from db import get_db

    conn = get_db()
    yield conn
    conn.close()


def _blind_report(hits=3):
    """The SRP-0 ARF-2026-920016 signature: rows with UUID names, no match types."""
    return {
        "provider": "complyadvantage",
        "screened_at": "2026-06-25T11:50:30",
        "screening_mode": "live",
        "total_hits": hits,
        "company_screening": {
            "provider": "complyadvantage", "api_status": "live", "matched": True,
            "sanctions": {
                "api_status": "live", "matched": True,
                "results": [
                    {
                        "name": f"019efe9d-8e89-71eb-bc51-53947aa4b{n:03x}",
                        "profile_identifier": f"019efe9d-8e89-71eb-bc51-53947aa4b{n:03x}",
                        "match_category": "other",
                    }
                    for n in range(hits)
                ],
            },
        },
        "director_screenings": [], "ubo_screenings": [], "intermediary_screenings": [],
        "overall_flags": [],
    }


def _enriched_report():
    return {
        "provider": "complyadvantage",
        "screened_at": "2026-07-17T00:00:00",
        "screening_mode": "live",
        "total_hits": 1,
        "company_screening": {
            "provider": "complyadvantage", "api_status": "live", "matched": True,
            "sanctions": {
                "api_status": "live", "matched": True,
                "results": [{
                    "name": "Real Matched Entity Ltd",
                    "profile_identifier": "019f0000-0000-7000-8000-000000000001",
                    "provider_match_types": ["name_exact"],
                    "match_categories": ["sanctions"],
                }],
            },
        },
        "director_screenings": [], "ubo_screenings": [], "intermediary_screenings": [],
        "overall_flags": ["sanctions"],
    }


def _seed_app(db, ref, app_id, report, *, is_fixture=False):
    db.execute("DELETE FROM applications WHERE ref = ?", (ref,))
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, is_fixture)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, ref, "testclient001", f"{ref} Co Ltd", "Mauritius", "Technology",
         "SME", "in_review", json.dumps({"screening_report": report}), is_fixture),
    )
    db.commit()


def _wipe(db):
    db.execute("DELETE FROM directors WHERE application_id LIKE 'srp2t%'")
    db.execute("DELETE FROM applications WHERE id LIKE 'srp2t%'")
    db.commit()


def test_classify_report_classes():
    assert classify_report(_blind_report())["clazz"] == "pre_enrichment"
    assert classify_report(_enriched_report())["clazz"] == "enriched"
    empty = _blind_report(hits=0)
    empty["company_screening"]["sanctions"]["results"] = []
    assert classify_report(empty)["clazz"] == "no_hits"
    info = classify_report(_blind_report(hits=5))
    assert info["hit_rows"] == 5
    assert info["rows_with_real_name"] == 0
    assert info["rows_with_match_types"] == 0


def test_selection_targets_only_blind_nonfixture_reports(db):
    _wipe(db)
    _seed_app(db, "ARF-SRP2T-001", "srp2t000000000001", _blind_report())
    _seed_app(db, "ARF-SRP2T-002", "srp2t000000000002", _enriched_report())
    _seed_app(db, "ARF-SRP2T-003", "srp2t000000000003", _blind_report(), is_fixture=True)

    candidates, skipped = select_candidates(db, limit=25)
    refs = {c["ref"] for c in candidates}
    assert "ARF-SRP2T-001" in refs
    assert "ARF-SRP2T-002" not in refs   # enriched — not a candidate
    assert "ARF-SRP2T-003" not in refs   # fixture — excluded entirely

    # Explicit --refs targeting bypasses auto-classification (founder override)
    explicit, _ = select_candidates(db, refs=["ARF-SRP2T-002"], limit=25)
    assert {c["ref"] for c in explicit} == {"ARF-SRP2T-002"}


def test_adjudicated_applications_are_skipped_not_refreshed(db):
    _wipe(db)
    _seed_app(db, "ARF-SRP2T-010", "srp2t000000000010", _blind_report())
    db.execute("DELETE FROM screening_reviews WHERE application_id = ?", ("srp2t000000000010",))
    db.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition, reviewer_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("srp2t000000000010", "entity", "ARF-SRP2T-010 Co Ltd", "cleared", "Officer One"),
    )
    db.commit()

    candidates, skipped = select_candidates(db, limit=25)
    assert "ARF-SRP2T-010" not in {c["ref"] for c in candidates}
    blocked = [s for s in skipped if s["ref"] == "ARF-SRP2T-010"]
    assert blocked and "officer-driven refresh required" in blocked[0]["skip_reason"]

    # The guard also applies to explicit --refs targeting.
    explicit, explicit_skipped = select_candidates(db, refs=["ARF-SRP2T-010"], limit=25)
    assert explicit == []
    assert any(s["ref"] == "ARF-SRP2T-010" for s in explicit_skipped)


def test_dry_run_mutates_nothing(db):
    _wipe(db)
    _seed_app(db, "ARF-SRP2T-020", "srp2t000000000020", _blind_report())
    before = db.execute(
        "SELECT prescreening_data FROM applications WHERE id = ?", ("srp2t000000000020",)
    ).fetchone()["prescreening_data"]

    summary = refresh_stale_screening_reports(db, execute=False, limit=5)
    assert summary["mode"] == "dry_run"
    assert any(c["ref"] == "ARF-SRP2T-020" for c in summary["candidates"])
    assert summary["refreshed"] == []

    after = db.execute(
        "SELECT prescreening_data FROM applications WHERE id = ?", ("srp2t000000000020",)
    ).fetchone()["prescreening_data"]
    assert after == before
    archived = db.execute(
        "SELECT COUNT(*) AS n FROM screening_report_archive WHERE application_id = ?",
        ("srp2t000000000020",),
    ).fetchone()["n"]
    assert archived == 0


def test_execute_archives_replaces_and_audit_chains(db):
    _wipe(db)
    _seed_app(db, "ARF-SRP2T-030", "srp2t000000000030", _blind_report())

    def fake_rescreen(application_id):
        fresh = _enriched_report()
        row = db.execute(
            "SELECT prescreening_data FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        prescreening = json.loads(row["prescreening_data"])
        prescreening["screening_report"] = fresh
        db.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps(prescreening), application_id),
        )
        db.commit()
        return {"total_hits": 1}

    summary = refresh_stale_screening_reports(
        db, execute=True, limit=5, refs=["ARF-SRP2T-030"],
        rescreen_fn=fake_rescreen, pace_seconds=0,
    )
    assert len(summary["refreshed"]) == 1
    result = summary["refreshed"][0]
    assert result["new_class"] == "enriched"
    assert result["new_rows_with_real_name"] == 1
    assert result["old_report_hash"] != result["new_report_hash"]

    archive = db.execute(
        "SELECT * FROM screening_report_archive WHERE application_id = ?",
        ("srp2t000000000030",),
    ).fetchall()
    assert len(archive) == 1
    archived_report = json.loads(archive[0]["report_json"])
    assert classify_report(archived_report)["clazz"] == "pre_enrichment"  # OLD report preserved
    assert archive[0]["report_hash"] == result["old_report_hash"]
    assert archive[0]["archived_by"] == ARCHIVE_ACTOR

    audit = db.execute(
        "SELECT * FROM audit_log WHERE action = 'srp2_screening_report_refreshed' AND target = ?",
        ("ARF-SRP2T-030",),
    ).fetchall()
    assert len(audit) == 1
    detail = json.loads(audit[0]["detail"])
    assert detail["old_report_hash"] == result["old_report_hash"]
    assert detail["new_report_hash"] == result["new_report_hash"]

    # Re-run safety: freshly refreshed app is not re-selected (enriched now,
    # and recently archived even if it were not).
    candidates, skipped = select_candidates(db, limit=25)
    assert "ARF-SRP2T-030" not in {c["ref"] for c in candidates}


def test_failed_rescreen_preserves_archive_and_original_report(db):
    _wipe(db)
    _seed_app(db, "ARF-SRP2T-040", "srp2t000000000040", _blind_report())
    before = db.execute(
        "SELECT prescreening_data FROM applications WHERE id = ?", ("srp2t000000000040",)
    ).fetchone()["prescreening_data"]

    def failing_rescreen(application_id):
        raise RuntimeError("provider unavailable")

    summary = refresh_stale_screening_reports(
        db, execute=True, limit=5, refs=["ARF-SRP2T-040"],
        rescreen_fn=failing_rescreen, pace_seconds=0,
    )
    assert summary["refreshed"] == []
    assert len(summary["failed"]) == 1
    assert "provider unavailable" in summary["failed"][0]["error"]

    # Archive-first ordering: snapshot exists even though the refresh failed…
    archived = db.execute(
        "SELECT COUNT(*) AS n FROM screening_report_archive WHERE application_id = ?",
        ("srp2t000000000040",),
    ).fetchone()["n"]
    assert archived == 1
    # …and the live report is untouched.
    after = db.execute(
        "SELECT prescreening_data FROM applications WHERE id = ?", ("srp2t000000000040",)
    ).fetchone()["prescreening_data"]
    assert after == before


def test_refuses_production(db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        refresh_stale_screening_reports(db, execute=False, limit=1)


def test_archive_table_is_regulated():
    from regulated_deletion import RegulatedDeleteDenied, assert_regulated_delete_allowed

    with pytest.raises(RegulatedDeleteDenied):
        assert_regulated_delete_allowed("screening_report_archive")


def test_force_refresh_bypasses_recent_archive_guard(db):
    """Codex batch-1 retry need: after a failed batch the apps are recently
    archived; --force-refresh must re-admit them, without weakening the
    adjudication guard."""
    from refresh_stale_screening_reports import archive_current_report

    _wipe(db)
    _seed_app(db, "ARF-SRP2T-060", "srp2t000000000060", _blind_report())
    app = {"id": "srp2t000000000060", "ref": "ARF-SRP2T-060"}
    archive_current_report(db, app, _blind_report())
    db.commit()

    normal, skipped = select_candidates(db, refs=["ARF-SRP2T-060"], limit=5)
    assert normal == []
    assert any("already refreshed" in s0.get("skip_reason", "") for s0 in skipped)

    forced, _ = select_candidates(db, refs=["ARF-SRP2T-060"], limit=5, force_refresh=True)
    assert {c["ref"] for c in forced} == {"ARF-SRP2T-060"}


def test_batch_limit_is_capped(db):
    _wipe(db)
    for n in range(3):
        _seed_app(db, f"ARF-SRP2T-05{n}", f"srp2t00000000005{n}", _blind_report())
    summary = refresh_stale_screening_reports(db, execute=False, limit=999)
    assert summary["batch_limit"] == 25  # MAX_BATCH_LIMIT
    summary = refresh_stale_screening_reports(db, execute=False, limit=2)
    assert len(summary["candidates"]) <= 2
