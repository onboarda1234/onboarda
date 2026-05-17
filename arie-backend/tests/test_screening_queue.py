import json


def test_screening_queue_payload_uses_application_level_metrics(db, temp_db):
    from server import _build_screening_queue_payload

    queue_user = {"type": "client", "sub": "screening_queue_metrics_client"}
    baseline = _build_screening_queue_payload(db, queue_user)

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("app_pending", "ARF-PENDING", queue_user["sub"], "Pending Co", "Mauritius", "Technology", "SME", "pricing_review", json.dumps({})),
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
            queue_user["sub"],
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

    payload = _build_screening_queue_payload(db, queue_user)

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


def test_screening_queue_uses_company_adverse_media_as_entity_provider_truth(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_company_media",
            "ARF-COMPANY-MEDIA",
            "client_media",
            "Company Media Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-01-03T00:00:00Z",
                        "screening_mode": "live",
                        "company_screening": {
                            "provider": "complyadvantage",
                            "source": "complyadvantage",
                            "api_status": "live",
                            "screened_at": "2026-01-03T00:00:00Z",
                            "matched": True,
                            "sanctions": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                            "adverse_media": {
                                "matched": True,
                                "source": "complyadvantage",
                                "api_status": "live",
                                "results": [{
                                    "name": "Company Media Ltd",
                                    "is_adverse_media": True,
                                    "match_categories": ["adverse_media"],
                                    "provider_risk_identifier": "risk-media-1",
                                }],
                            },
                        },
                        "director_screenings": [],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": ["ComplyAdvantage adverse media hit: Company Media Ltd"],
                        "total_hits": 1,
                    }
                }
            ),
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-COMPANY-MEDIA" and r["subject_type"] == "entity")

    assert row["status_key"] == "review_required"
    assert row["status_label"] == "Review Required"
    assert row["screening_state"] == "completed_match"
    assert row["total_hits"] == 1
    assert row["screened_at"] == "2026-01-03T00:00:00Z"
    assert "Company adverse media match" in row["entity_context"]


def test_screening_queue_uses_top_level_company_results_when_subrecords_are_clear(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_company_top_level_media",
            "ARF-COMPANY-TOP-MEDIA",
            "client_media_top",
            "Company Top Media Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-01-03T00:00:00Z",
                        "screening_mode": "live",
                        "company_screening": {
                            "provider": "complyadvantage",
                            "source": "complyadvantage",
                            "api_status": "live",
                            "screened_at": "2026-01-03T00:00:00Z",
                            "matched": False,
                            "results": [{
                                "name": "Company Top Media Ltd",
                                "is_adverse_media": True,
                                "match_categories": ["adverse_media"],
                                "provider_risk_identifier": "risk-company-top-media-1",
                            }],
                            "sanctions": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                            "adverse_media": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                        },
                        "director_screenings": [],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": ["ComplyAdvantage adverse media hit: Company Top Media Ltd"],
                        "total_hits": 1,
                    }
                }
            ),
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-COMPANY-TOP-MEDIA" and r["subject_type"] == "entity")

    assert row["status_key"] == "review_required"
    assert row["status_label"] == "Review Required"
    assert row["screening_state"] == "completed_match"
    assert row["watchlist_status"] == "match"
    assert row["review_required"] is True
    assert row["total_hits"] == 1
    assert "Company adverse media match" in row["entity_context"]


def test_screening_queue_surfaces_undeclared_provider_pep(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_undeclared_pep",
            "ARF-UNDECLARED-PEP",
            "client_pep",
            "Undeclared Pep Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-01-04T00:00:00Z",
                        "screening_mode": "live",
                        "company_screening": {
                            "source": "complyadvantage",
                            "api_status": "live",
                            "sanctions": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                        },
                        "director_screenings": [{
                            "person_name": "Provider Pep",
                            "person_type": "director",
                            "declared_pep": "No",
                            "provider_detected_pep": True,
                            "undeclared_pep": True,
                            "has_pep_hit": True,
                            "screening": {
                                "matched": True,
                                "source": "complyadvantage",
                                "api_status": "live",
                                "screened_at": "2026-01-04T00:00:00Z",
                                "results": [{
                                    "name": "Provider Pep",
                                    "is_pep": True,
                                    "match_categories": ["PEP"],
                                    "provider_risk_identifier": "risk-pep-1",
                                }],
                            },
                        }],
                        "ubo_screenings": [],
                        "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                        "kyc_applicants": [],
                        "overall_flags": ["ComplyAdvantage PEP hit: Provider Pep"],
                        "total_hits": 1,
                    }
                }
            ),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_undeclared_pep", "Provider Pep", "Mauritius", "Yes"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-UNDECLARED-PEP" and r["subject_name"] == "Provider Pep")

    assert row["pep_declared_status"] == "not_declared"
    assert row["pep_screening_status"] == "match"
    assert row["status_key"] == "review_required"
    assert row["screened_at"] == "2026-01-04T00:00:00Z"
    assert "Undeclared PEP" in row["entity_context"]
    assert "Declared PEP" not in row["entity_context"]


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
