import json


def test_upsert_screening_review_persists_and_survives_queue_reload(db, temp_db):
    from server import _build_screening_queue_payload, upsert_screening_review

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_reviewed",
            "ARF-REVIEWED",
            "client_reviewed",
            "Reviewed Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-01-03T00:00:00",
                        "screening_mode": "live",
                        "company_screening": {
                            "found": True,
                            "source": "opencorporates",
                            "api_status": "live",
                            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                        },
                        "director_screenings": [
                            {
                                "person_name": "Alice Review",
                                "person_type": "director",
                                "declared_pep": "No",
                                "screening": {
                                    "matched": True,
                                    "results": [{"is_sanctioned": True, "is_pep": False}],
                                    "source": "sumsub",
                                    "api_status": "live",
                                },
                            }
                        ],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi", "api_status": "live"},
                        "kyc_applicants": [],
                        "overall_flags": ["Director 'Alice Review' has sanctions/PEP matches"],
                        "total_hits": 1,
                    }
                }
            ),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_reviewed", "Alice Review", "Mauritius", "No"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-REVIEWED" and r["subject_name"] == "Alice Review")
    assert row["status_key"] == "review_required"
    assert row["review_disposition"] is None

    upsert_screening_review(
        db,
        "app_reviewed",
        "director",
        "Alice Review",
        "cleared",
        "Reviewed and cleared after manual false positive assessment.",
        "admin001",
        "Test Admin",
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-REVIEWED" and r["subject_name"] == "Alice Review")
    assert row["review_disposition"] == "cleared"
    assert "false positive" in row["review_notes"].lower()
    assert row["reviewed_by"] == "Test Admin"


def test_screening_review_rationale_flows_into_memo():
    from memo_handler import build_compliance_memo

    app = {
        "id": "app_memo_screening_review",
        "ref": "ARF-MEMO-SCREENING-REVIEW",
        "company_name": "Memo Screening Review Ltd",
        "brn": "BRN-123",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "risk_level": "MEDIUM",
        "risk_score": 45,
        "prescreening_data": json.dumps({
            "screening_report": {
                "company_screening": {"sanctions": {"matched": False, "results": [], "api_status": "live"}},
                "director_screenings": [],
                "ubo_screenings": [],
            }
        }),
        "screening_reviews": [{
            "subject_type": "entity",
            "subject_name": "Memo Screening Review Ltd",
            "disposition": "cleared",
            "disposition_code": "provider_no_relevant_match",
            "rationale": "Officer confirmed no relevant provider match after review.",
            "reviewer_name": "Test Admin",
            "requires_four_eyes": False,
        }],
    }

    memo, _, _, _ = build_compliance_memo(app, [], [], [])
    content = memo["sections"]["screening_results"]["content"]
    assert "Officer disposition evidence" in content
    assert "provider_no_relevant_match" in content
    assert "Officer confirmed no relevant provider match" in content
