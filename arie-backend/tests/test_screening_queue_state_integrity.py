import json

import pytest


def _resolve(row):
    from screening_state import resolve_screening_queue_state

    return resolve_screening_queue_state(row)


def test_pending_raw_terminal_no_hits_fails_closed_to_in_progress():
    resolved = _resolve({
        "status_key": "screening_pending",
        "status_label": "Screening Pending Provider",
        "screening_state": "completed_clear",
        "terminal": True,
        "total_hits": 0,
    })

    assert resolved["status_key"] == "screening_in_progress"
    assert resolved["canonical_status_key"] == "screening_in_progress"
    assert resolved["canonical_status"] == "Screening In Progress"
    assert resolved["officer_label"] == "Screening In Progress"
    assert resolved["provider_status"] == "pending"
    assert resolved["screening_provider_status"] == "pending"
    assert resolved["provider_status_scope"] == "aml_pep_sanctions_screening"
    assert resolved["terminal"] is False
    assert resolved["is_terminal"] is False
    assert resolved["has_hits"] is False
    assert resolved["review_evidence_present"] is False
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is False
    assert "screening_not_terminal" in resolved["blocking_flags"]
    assert resolved["reasons"] == ["Provider screening is still in progress."]
    assert "terminal_true_with_non_terminal_provider_status" in resolved["state_integrity_flags"]


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
    assert resolved["canonical_status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
    assert resolved["provider_status"] == "pending"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True
    assert "unreviewed_hits_claimed_clear" in resolved["state_integrity_flags"]
    assert "unreviewed_hits_claimed_defensible_clear" in resolved["state_integrity_flags"]
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
    assert resolved["canonical_status_key"] == "clear"
    assert resolved["canonical_status"] == "Clear"
    assert resolved["officer_review_status"] == "cleared"
    assert resolved["review_evidence_present"] is True
    assert resolved["defensible_clear"] is True
    assert resolved["review_required"] is False
    assert resolved["state_integrity_flags"] == []


def test_hits_claimed_defensible_clear_requires_explicit_officer_review():
    resolved = _resolve({
        "screening_state": "completed_match",
        "terminal": True,
        "total_hits": 1,
        "screening_result": "clear",
        "defensible_clear": True,
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
    assert resolved["provider_status"] == "completed_match"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True
    assert resolved["officer_review_status"] == "not_reviewed"
    assert "unreviewed_hits_claimed_clear" in resolved["state_integrity_flags"]
    assert "unreviewed_hits_claimed_defensible_clear" in resolved["state_integrity_flags"]


def test_officer_clear_cannot_finalize_non_terminal_provider_result():
    resolved = _resolve({
        "screening_state": "pending_provider",
        "terminal": False,
        "total_hits": 1,
        "screening_result": "match",
        "review_disposition": "cleared",
        "review_disposition_code": "false_positive_cleared",
        "review_actionable": False,
        "reviewer_id": "co001",
        "review_rationale": "Officer reviewed stale match.",
        "reviewed_at": "2026-06-10T10:00:00Z",
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
    assert resolved["provider_status"] == "pending"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True
    assert "officer_clear_with_non_terminal_provider" in resolved["state_integrity_flags"]


def test_officer_clear_cannot_finalize_missing_terminal_provider_evidence():
    resolved = _resolve({
        "total_hits": 1,
        "screening_result": "match",
        "review_disposition": "cleared",
        "review_disposition_code": "false_positive_cleared",
        "review_actionable": False,
        "reviewer_id": "co001",
        "review_rationale": "Officer reviewed a match but terminal provider evidence is absent.",
        "reviewed_at": "2026-06-10T10:00:00Z",
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
    assert resolved["defensible_clear"] is False
    assert resolved["review_required"] is True
    assert "officer_clear_without_terminal_provider" in resolved["state_integrity_flags"]


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
    assert resolved["canonical_status"] == "Failed / Provider Error"
    assert resolved["provider_status"] == "failed"
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
    assert resolved["canonical_status_key"] == "clear"
    assert resolved["provider_status"] == "completed_clear"
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
    assert resolved["provider_status"] == "pending"
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
    assert resolved["canonical_status"] == "Failed / Provider Error"
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
    assert resolved["canonical_status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
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
    clear_keys = {"clear"}
    for row in rows:
        status_key = row.get("canonical_status_key") or row["status_key"]
        hits = int(row.get("total_hits") or 0)
        officer_cleared = row.get("status_key") == "cleared_by_officer" and row.get("officer_review_status") == "cleared"
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

    assert row["status_key"] == "screening_in_progress"
    assert row["canonical_status_key"] == "screening_in_progress"
    assert row["canonical_status"] == "Screening In Progress"
    assert row["status_label"] == "Screening In Progress"
    assert row["provider_status"] == "pending"
    assert row["officer_review_status"] == "not_reviewed"
    assert "non_terminal_claimed_clear" in row["state_integrity_flags"]
    assert row["defensible_clear"] is False
    assert row["review_required"] is False
    assert row["screening_state"] == "pending_provider"
    assert row["screening_result"] == "pending"
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
    for field in (
        "canonical_status",
        "officer_label",
        "provider_status",
        "screening_provider_status",
        "provider_status_scope",
        "terminal",
        "is_terminal",
        "total_hits",
        "has_hits",
        "officer_review_status",
        "defensible_clear",
        "requires_review",
        "review_evidence_present",
        "state_integrity_flags",
        "blocking_flags",
        "reasons",
    ):
        assert field in hit_row
    assert hit_row["status_key"] == "review_required"
    assert hit_row["canonical_status"] == "Review Required"
    assert hit_row["status_label"] == "Review Required"
    assert hit_row["provider_status"] == "pending"
    assert hit_row["screening_provider_status"] == "pending"
    assert hit_row["provider_status_scope"] == "aml_pep_sanctions_screening"
    assert hit_row["is_terminal"] is False
    assert hit_row["has_hits"] is True
    assert hit_row["requires_review"] is True
    assert hit_row["review_evidence_present"] is False
    assert "unresolved_screening_hits" in hit_row["blocking_flags"]
    assert hit_row["reasons"]
    assert hit_row["defensible_clear"] is False
    assert hit_row["review_required"] is True
    assert "raw_status" in hit_row
