import json

import pytest


def _resolve(row):
    from screening_state import resolve_screening_queue_state

    return resolve_screening_queue_state(row)


def test_pending_raw_terminal_no_hits_resolves_to_clear():
    resolved = _resolve({
        "status_key": "screening_pending",
        "status_label": "Screening Pending Provider",
        "screening_state": "completed_clear",
        "terminal": True,
        "total_hits": 0,
    })

    assert resolved["status_key"] == "clear"
    assert resolved["canonical_status"] == "Clear"
    assert resolved["defensible_clear"] is True
    assert resolved["review_required"] is False


def test_pending_clear_with_hits_fails_closed_to_review_required():
    resolved = _resolve({
        "status_key": "screening_pending",
        "status_label": "No Match",
        "screening_state": "not_started",
        "screening_result": "clear",
        "terminal": True,
        "total_hits": 2,
        "defensible_clear": True,
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True
    assert "Conflicting" in resolved["screening_queue_reason"]


def test_terminal_hits_without_officer_disposition_requires_review():
    resolved = _resolve({
        "screening_state": "completed_match",
        "terminal": True,
        "total_hits": 1,
        "screening_result": "match",
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True


def test_terminal_hits_cleared_by_officer_get_distinct_status():
    resolved = _resolve({
        "screening_state": "completed_match",
        "terminal": True,
        "total_hits": 1,
        "screening_result": "match",
        "review_disposition": "cleared",
        "review_disposition_code": "false_positive_cleared",
        "review_actionable": False,
        "reviewer_id": "co001",
        "review_rationale": "Officer confirmed this is a false-positive provider hit.",
        "reviewed_at": "2026-06-10T10:00:00Z",
    })

    assert resolved["status_key"] == "cleared_by_officer"
    assert resolved["canonical_status"] == "Cleared by Officer"
    assert resolved["defensible_clear"] is True
    assert resolved["review_required"] is False


def test_no_hit_officer_clear_does_not_override_provider_failure():
    resolved = _resolve({
        "screening_state": "failed",
        "terminal": False,
        "total_hits": 0,
        "review_disposition": "cleared",
        "review_disposition_code": "false_positive_cleared",
        "review_actionable": False,
    })

    assert resolved["status_key"] == "failed"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True


def test_terminal_no_hits_is_clear():
    resolved = _resolve({
        "screening_state": "completed_clear",
        "terminal": True,
        "total_hits": 0,
        "screening_result": "clear",
    })

    assert resolved["status_key"] == "clear"
    assert resolved["defensible_clear"] is True
    assert resolved["review_required"] is False


def test_non_terminal_unknown_hits_is_screening_in_progress():
    resolved = _resolve({
        "screening_state": "pending_provider",
        "terminal": False,
        "screening_result": "unknown",
    })

    assert resolved["status_key"] == "screening_in_progress"
    assert resolved["canonical_status"] == "Screening In Progress"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is False


@pytest.mark.parametrize(
    "row",
    [
        {"screening_state": "failed", "terminal": False, "total_hits": 0},
        {"screening_state": "completed_clear", "normalized_screening_state": "failed", "terminal": True, "total_hits": 0},
    ],
)
def test_provider_error_or_failed_conflict_is_failed(row):
    resolved = _resolve(row)

    assert resolved["status_key"] == "failed"
    assert resolved["canonical_status"] == "Failed"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True


def test_escalated_officer_disposition_drives_escalated():
    resolved = _resolve({
        "screening_state": "completed_match",
        "terminal": True,
        "total_hits": 1,
        "review_disposition": "escalated",
        "review_disposition_code": "confirmed_match",
        "review_actionable": False,
    })

    assert resolved["status_key"] == "escalated"
    assert resolved["canonical_status"] == "Escalated"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is False


def test_follow_up_officer_disposition_drives_follow_up_required():
    resolved = _resolve({
        "screening_state": "completed_match",
        "terminal": True,
        "total_hits": 1,
        "review_disposition": "follow_up_required",
        "review_disposition_code": "needs_more_information",
        "review_actionable": False,
    })

    assert resolved["status_key"] == "follow_up_required"
    assert resolved["canonical_status"] == "Follow-up Required"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is False


def test_legacy_only_screening_data_produces_canonical_status():
    resolved = _resolve({
        "screening_state": "completed_clear",
        "total_hits": 0,
        "screening_result": "clear",
    })

    assert resolved["status_key"] == "clear"
    assert resolved["defensible_clear"] is True


def test_normalized_only_screening_data_produces_canonical_status():
    resolved = _resolve({
        "normalized_screening_state": "completed_clear",
        "normalized_total_hits": 0,
        "screening_result": "clear",
    })

    assert resolved["status_key"] == "clear"
    assert resolved["canonical_status"] == "Clear"
    assert resolved["defensible_clear"] is True


def test_legacy_and_normalized_match_clear_conflict_fails_closed_to_review():
    resolved = _resolve({
        "screening_state": "completed_clear",
        "normalized_screening_state": "completed_match",
        "total_hits": 1,
        "screening_result": "clear",
        "terminal": True,
        "defensible_clear": True,
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True


def _assert_no_impossible_queue_states(rows):
    pending_keys = {"not_started", "screening_in_progress"}
    clear_keys = {"clear", "cleared_by_officer"}
    for row in rows:
        status_key = row["status_key"]
        hits = int(row.get("total_hits") or 0)
        officer_cleared = status_key == "cleared_by_officer"
        assert not (status_key in pending_keys and row.get("defensible_clear") is True), row
        assert not (status_key in pending_keys and row.get("screening_result") == "clear"), row
        assert not (row.get("defensible_clear") is True and hits > 0 and not officer_cleared), row
        assert not (status_key == "clear" and hits > 0 and not officer_cleared), row
        assert not (row.get("terminal") is False and status_key in clear_keys), row


def test_canonical_queue_payload_suppresses_top_level_legacy_clear_signal():
    from server import _apply_screening_queue_canonical_state

    row = _apply_screening_queue_canonical_state({
        "application_ref": "ARF-SQ1-STAGING-LEGACY",
        "subject_name": "Legacy Clear Pending Co",
        "subject_type": "entity",
        "status_key": "screening_pending",
        "status_label": "Screening Pending Provider",
        "screening_state": "not_started",
        "screening_result": "clear",
        "terminal": True,
        "defensible_clear": True,
        "review_required": True,
        "total_hits": 0,
    })

    assert row["status_key"] == "not_started"
    assert row["status_label"] == "Not Started"
    assert row["defensible_clear"] is False
    assert row["review_required"] is False
    assert row["screening_state"] == "not_started"
    assert row["screening_result"] == "not_started"
    assert row["terminal"] is False
    assert row["raw_status"]["screening_result"] == "clear"
    assert row["raw_status"]["defensible_clear"] is True
    _assert_no_impossible_queue_states([row])


def test_screening_queue_payload_cannot_expose_impossible_officer_states(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_sq1_impossible",
            "ARF-SQ1-IMPOSSIBLE",
            "client_sq1",
            "SQ1 Impossible Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-06-10T10:00:00Z",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True,
                        "source": "opencorporates",
                        "sanctions": {
                            "matched": False,
                            "results": [],
                            "source": "complyadvantage",
                            "api_status": "live",
                        },
                    },
                    "director_screenings": [{
                        "person_name": "SQ1 Pending Hit",
                        "person_type": "director",
                        "declared_pep": "No",
                        "screening": {
                            "matched": False,
                            "results": [{
                                "name": "SQ1 Pending Hit",
                                "is_adverse_media": True,
                                "match_categories": ["adverse_media"],
                            }],
                            "source": "complyadvantage",
                            "api_status": "pending",
                        },
                    }],
                    "ubo_screenings": [],
                    "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                    "kyc_applicants": [],
                    "overall_flags": [],
                    "total_hits": 1,
                }
            }),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_sq1_impossible", "SQ1 Pending Hit", "Mauritius", "No"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    rows = [row for row in payload["rows"] if row["application_ref"] == "ARF-SQ1-IMPOSSIBLE"]

    assert rows
    _assert_no_impossible_queue_states(rows)
    hit_row = next(row for row in rows if row["subject_name"] == "SQ1 Pending Hit")
    assert hit_row["status_key"] == "review_required"
    assert hit_row["status_label"] == "Review Required"
    assert hit_row["defensible_clear"] is False
    assert hit_row["review_required"] is True
    assert "raw_status" in hit_row
