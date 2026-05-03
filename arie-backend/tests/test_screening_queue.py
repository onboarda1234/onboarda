import json


def test_screening_queue_payload_uses_application_level_metrics(db, temp_db):
    from server import _build_screening_queue_payload

    baseline = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("app_pending", "ARF-PENDING", "client_1", "Pending Co", "Mauritius", "Technology", "SME", "pricing_review", json.dumps({})),
    )

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_review",
            "ARF-REVIEW",
            "client_2",
            "Review Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-01-01T00:00:00",
                        "screening_mode": "live",
                        "company_screening": {
                            "found": False,
                            "source": "opencorporates",
                            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                        },
                        "director_screenings": [],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": ["Company 'Review Co' not found in corporate registry"],
                        "total_hits": 0,
                    }
                }
            ),
        ),
    )

    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})

    assert payload["metrics"]["applications_awaiting_screening"] == baseline["metrics"]["applications_awaiting_screening"] + 1
    assert payload["metrics"]["applications_screened"] == baseline["metrics"]["applications_screened"] + 1
    assert payload["metrics"]["applications_requiring_review"] == baseline["metrics"]["applications_requiring_review"] + 1

    review_company_row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-REVIEW" and r["subject_type"] == "entity")
    assert review_company_row["status_key"] == "review_required"
    assert "Registry not found" in review_company_row["entity_context"]


def test_screening_queue_separates_declared_pep_from_provider_pep(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_pep",
            "ARF-PEP",
            "client_3",
            "PEP Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-01-02T00:00:00",
                        "screening_mode": "live",
                        "company_screening": {
                            "found": True,
                            "source": "opencorporates",
                            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                        },
                        "director_screenings": [
                            {
                                "person_name": "Declared Pep",
                                "person_type": "director",
                                "declared_pep": "Yes",
                                "screening": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                            }
                        ],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": [],
                        "total_hits": 0,
                    }
                }
            ),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_pep", "Declared Pep", "Mauritius", "Yes"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    person_row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-PEP" and r["subject_name"] == "Declared Pep")

    assert person_row["pep_declared_status"] == "declared"
    assert person_row["pep_screening_status"] == "clear"
    assert person_row["status_key"] == "declared_pep_review"


def test_screening_queue_does_not_label_not_configured_entity_as_live(temp_db):
    from server import _build_screening_queue_payload
    from db import get_db

    db = get_db()
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_not_configured_mode",
            "ARF-NOT-CONFIGURED-MODE",
            "client_4",
            "Not Configured Mode Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screening_mode": "live",
                        "company_screening": {
                            "found": True,
                            "source": "opencorporates",
                            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "not_configured"},
                        },
                        "director_screenings": [],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": [],
                        "total_hits": 0,
                    }
                }
            ),
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    db.close()
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-NOT-CONFIGURED-MODE")

    assert row["status_key"] == "screening_not_configured"
    assert row["screening_mode"] == "not_configured"


def test_screening_queue_marks_smoke_provider_as_simulated(temp_db):
    from server import _build_screening_queue_payload
    from db import get_db

    db = get_db()
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_smoke_mode",
            "ARF-SMOKE-MODE",
            "client_5",
            "Smoke Mode Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screening_mode": "live",
                        "company_screening": {
                            "found": True,
                            "source": "opencorporates",
                            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                        },
                        "director_screenings": [
                            {
                                "person_name": "Smoke Director",
                                "screening": {
                                    "matched": True,
                                    "results": [{"is_sanctioned": True, "name": "Smoke Director"}],
                                    "source": "codex-smoke",
                                    "api_status": "live",
                                },
                            }
                        ],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": [],
                        "total_hits": 1,
                    }
                }
            ),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_smoke_mode", "Smoke Director", "Mauritius", "No"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    db.close()
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SMOKE-MODE" and r["subject_name"] == "Smoke Director")

    assert row["status_key"] == "review_required"
    assert row["screening_mode"] == "simulated"
