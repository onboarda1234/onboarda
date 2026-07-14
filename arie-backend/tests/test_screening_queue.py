import json


def test_queue_resolver_quarantines_stale_clear_claim():
    from screening_state import resolve_screening_queue_state

    resolved = resolve_screening_queue_state({
        "status_key": "screened_no_match",
        "status_label": "No Match",
        "screening_state": "completed_clear",
        "screening_truth_state": "stale",
        "provider_availability": "stale",
        "screening_result": "clear",
        "terminal": True,
        "defensible_clear": True,
        "total_hits": 0,
    })

    assert resolved["status_key"] == "stale"
    assert resolved["canonical_status"] == "Stale / Requires Refresh"
    assert resolved["terminal"] is False
    assert resolved["defensible_clear"] is False
    assert "screening_stale_requires_refresh" in resolved["blocking_flags"]
    assert "stale_screening_claimed_clear" in resolved["state_integrity_flags"]


def test_queue_resolver_blocks_clear_with_partial_evidence():
    from screening_state import resolve_screening_queue_state

    resolved = resolve_screening_queue_state({
        "status_key": "screened_no_match",
        "status_label": "No Match",
        "screening_state": "completed_clear",
        "screening_truth_state": "completed_clear",
        "provider_availability": "available",
        "screening_result": "clear",
        "terminal": True,
        "defensible_clear": True,
        "total_hits": 0,
        "evidence_quality": "partial",
        "missing_reason": "missing_provider_identifiers",
    })

    assert resolved["status_key"] == "review_required"
    assert resolved["canonical_status"] == "Review Required"
    assert resolved["terminal"] is False
    assert "provider_evidence_incomplete" in resolved["blocking_flags"]
    assert "incomplete_evidence_claimed_clear" in resolved["state_integrity_flags"]


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
    assert person_row["status_key"] == "review_required"
    assert person_row["status_label"] == "Review Required"


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


def test_entity_row_does_not_inherit_person_level_application_hits(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_entity_person_hits",
            "ARF-ENTITY-PERSON-HITS",
            "client_entity_person_hits",
            "Entity Person Hits Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-01-03T00:00:00Z",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True,
                        "provider": "complyadvantage",
                        "source": "complyadvantage",
                        "api_status": "live",
                        "matched": False,
                        "results": [],
                        "sanctions": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                        "adverse_media": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                    },
                    "director_screenings": [{
                        "person_name": "Person Hit",
                        "person_type": "director",
                        "declared_pep": "No",
                        "screening": {
                            "matched": True,
                            "results": [{
                                "name": "Person Hit",
                                "match_categories": ["sanction"],
                                "provider_risk_identifier": "risk-person-hit-1",
                            }],
                            "source": "complyadvantage",
                            "api_status": "live",
                        },
                    }],
                    "ubo_screenings": [],
                    "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                    "kyc_applicants": [],
                    "overall_flags": ["ComplyAdvantage sanctions hit: Person Hit"],
                    "total_hits": 1,
                }
            }),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_entity_person_hits", "Person Hit", "Mauritius", "No"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    rows = [r for r in payload["rows"] if r["application_ref"] == "ARF-ENTITY-PERSON-HITS"]
    entity_row = next(r for r in rows if r["subject_type"] == "entity")
    person_row = next(r for r in rows if r["subject_name"] == "Person Hit")

    assert entity_row["total_hits"] == 0
    assert entity_row["status_key"] == "clear"
    assert entity_row["screening_truth_state"] == "completed_clear"
    assert entity_row["defensible_clear"] is True
    assert person_row["total_hits"] == 1
    assert person_row["status_key"] == "review_required"
    assert person_row["defensible_clear"] is False


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

    assert row["status_key"] == "failed"
    assert row["status_label"] == "Failed"
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


def _insert_sq2_screened_director(
    db,
    *,
    app_id,
    ref,
    subject_name="Evidence Director",
    case_id="case-sq2",
    alert_id="alert-sq2",
    risk_id="risk-sq2",
    profile_id="profile-sq2",
):
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            "client_sq2",
            "SQ2 Evidence Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps(
                {
                    "screening_report": {
                        "screened_at": "2026-06-01T00:00:00Z",
                        "screening_mode": "live",
                        "company_screening": {
                            "found": True,
                            "source": "complyadvantage",
                            "provider": "complyadvantage",
                            "sanctions": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                        },
                        "director_screenings": [
                            {
                                "person_name": subject_name,
                                "screening": {
                                    "matched": True,
                                    "source": "complyadvantage",
                                    "provider": "complyadvantage",
                                    "api_status": "live",
                                    "results": [
                                        {
                                            "name": subject_name,
                                            "is_adverse_media": True,
                                            "match_category": "Adverse Media",
                                            "match_categories": ["adverse_media"],
                                            "provider": "complyadvantage",
                                            "provider_case_identifier": case_id,
                                            "provider_alert_identifier": alert_id,
                                            "provider_risk_identifier": risk_id,
                                            "provider_profile_identifier": profile_id,
                                            "summary": "Legacy provider evidence shell",
                                        }
                                    ],
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
        (app_id, subject_name, "Mauritius", "No"),
    )


def _insert_sq2_ca_evidence(
    db,
    *,
    monitoring_id,
    app_id,
    case_id="case-sq2",
    alert_id="alert-sq2",
    matched_subject="Evidence Director",
    risk_id="risk-sq2",
    profile_id="profile-sq2",
    title="Provider article title",
    source_url="",
    source_available=0,
    evidence_hash="hash-sq2",
):
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, client_name, alert_type, severity, detected_by,
             summary, source_reference, status, provider, case_identifier, discovered_via)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            monitoring_id,
            app_id,
            "SQ2 Evidence Ltd",
            "media",
            "High",
            "complyadvantage",
            f"CA case {case_id} surfaced one media match",
            json.dumps({
                "provider": "complyadvantage",
                "case_identifier": case_id,
                "alert_identifier": alert_id,
                "risk_identifier": risk_id,
                "subject_scope": "director",
            }),
            "open",
            "complyadvantage",
            case_id,
            "manual",
        ),
    )
    db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier, alert_identifier,
             risk_identifier, profile_identifier, evidence_type, matched_subject_name,
             relationship_to_client, match_category, risk_indicator, match_confidence,
             source_title, source_name, source_url, source_url_available, publication_date,
             snippet, evidence_json, raw_provider_reference, evidence_status, evidence_hash, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            monitoring_id,
            app_id,
            "complyadvantage",
            case_id,
            alert_id,
            risk_id,
            profile_id,
            "adverse_media",
            matched_subject,
            "Director",
            "Adverse Media",
            "Adverse Media",
            "0.92",
            title,
            "Provider News",
            source_url,
            source_available,
            "2026-05-01",
            "Provider snippet for officer review",
            json.dumps({"title": title, "source_name": "Provider News"}),
            json.dumps({"risk_identifier": risk_id}),
            "fetched",
            evidence_hash,
            "2026-06-09T00:00:00Z",
        ),
    )


def test_screening_queue_summary_payload_omits_heavy_evidence_until_requested(db, temp_db):
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(
        db,
        app_id="app_sq4c_summary",
        ref="ARF-SQ4C-SUMMARY",
        case_id="case-sq4c-summary",
        alert_id="alert-sq4c-summary",
        risk_id="risk-sq4c-summary",
        profile_id="profile-sq4c-summary",
    )
    _insert_sq2_ca_evidence(
        db,
        monitoring_id=19401,
        app_id="app_sq4c_summary",
        case_id="case-sq4c-summary",
        alert_id="alert-sq4c-summary",
        risk_id="risk-sq4c-summary",
        profile_id="profile-sq4c-summary",
        evidence_hash="hash-sq4c-summary",
    )
    db.commit()

    summary_payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": "ARF-SQ4C-SUMMARY"},
        include_evidence=False,
    )
    row = next(r for r in summary_payload["rows"] if r["application_ref"] == "ARF-SQ4C-SUMMARY" and r["subject_type"] == "director")

    assert summary_payload["evidence_mode"] == "summary"
    assert row["evidence_detail_available"] is True
    assert row["provider_evidence_count"] >= 1
    assert "provider_evidence" not in row
    assert "items" not in row["screening_evidence"]
    assert "technical_details" not in row["screening_evidence"]
    assert row["evidence_summary"]["provider_risk_ids"] == ["risk-sq4c-summary"]

    full_payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": "ARF-SQ4C-SUMMARY"},
        include_evidence=True,
    )
    full_row = next(r for r in full_payload["rows"] if r["application_ref"] == "ARF-SQ4C-SUMMARY" and r["subject_type"] == "director")

    assert full_payload["evidence_mode"] == "full"
    assert full_row["provider_evidence"]
    assert full_row["screening_evidence"]["items"]
    assert full_row["screening_evidence"]["technical_details"]


def test_screening_queue_summary_defers_monitoring_evidence_hydration(db, temp_db, monkeypatch):
    import server
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(
        db,
        app_id="app_sq4c_summary_perf",
        ref="ARF-SQ4C-SUMMARY-PERF",
        case_id="case-sq4c-summary-perf",
        alert_id="alert-sq4c-summary-perf",
        risk_id="risk-sq4c-summary-perf",
        profile_id="profile-sq4c-summary-perf",
    )
    _insert_sq2_ca_evidence(
        db,
        monitoring_id=19411,
        app_id="app_sq4c_summary_perf",
        case_id="case-sq4c-summary-perf",
        alert_id="alert-sq4c-summary-perf",
        risk_id="risk-sq4c-summary-perf",
        profile_id="profile-sq4c-summary-perf",
        evidence_hash="hash-sq4c-summary-perf",
    )
    db.commit()

    def fail_monitoring_evidence_load(*args, **kwargs):
        raise AssertionError("summary queue list must not hydrate monitoring evidence")

    def fail_evidence_enrichment(*args, **kwargs):
        raise AssertionError("summary queue list must not run detail evidence enrichment")

    monkeypatch.setattr(server, "_load_monitoring_evidence_batch", fail_monitoring_evidence_load)
    monkeypatch.setattr(server, "_enrich_screening_queue_evidence", fail_evidence_enrichment)

    payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": "ARF-SQ4C-SUMMARY-PERF"},
        include_evidence=False,
    )
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ4C-SUMMARY-PERF" and r["subject_type"] == "director")

    assert payload["evidence_mode"] == "summary"
    assert row["status_key"] == "review_required"
    assert row["screening_truth_state"] == "completed_match"
    assert "provider_evidence" not in row
    assert "items" not in row["screening_evidence"]
    assert row["evidence_detail_available"] is True
    assert row["evidence_summary"]["provider_risk_ids"] == ["risk-sq4c-summary-perf"]


def test_screening_queue_full_evidence_still_hydrates_monitoring_evidence(db, temp_db, monkeypatch):
    import server
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(
        db,
        app_id="app_sq4c_full_perf",
        ref="ARF-SQ4C-FULL-PERF",
        case_id="case-sq4c-full-perf",
        alert_id="alert-sq4c-full-perf",
        risk_id="risk-sq4c-full-perf",
        profile_id="profile-sq4c-full-perf",
    )
    _insert_sq2_ca_evidence(
        db,
        monitoring_id=19412,
        app_id="app_sq4c_full_perf",
        case_id="case-sq4c-full-perf",
        alert_id="alert-sq4c-full-perf",
        risk_id="risk-sq4c-full-perf",
        profile_id="profile-sq4c-full-perf",
        evidence_hash="hash-sq4c-full-perf",
    )
    db.commit()

    original_loader = server._load_monitoring_evidence_batch
    calls = []

    def recording_loader(loader_db, app_ids):
        calls.append(list(app_ids))
        return original_loader(loader_db, app_ids)

    monkeypatch.setattr(server, "_load_monitoring_evidence_batch", recording_loader)

    payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": "ARF-SQ4C-FULL-PERF"},
        include_evidence=True,
    )
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ4C-FULL-PERF" and r["subject_type"] == "director")

    assert payload["evidence_mode"] == "full"
    assert calls
    assert row["provider_evidence"]
    assert row["screening_evidence"]["items"]
    assert row["screening_evidence"]["technical_details"]["linked_ca_1b_evidence_count"] == 1


def test_screening_queue_summary_read_is_read_only_and_does_not_call_providers(db, temp_db, monkeypatch):
    import server
    from server import _build_screening_queue_payload

    app_id = "app_sq4c_read_only_perf"
    app_ref = "ARF-SQ4C-READ-ONLY-PERF"
    _insert_sq2_screened_director(
        db,
        app_id=app_id,
        ref=app_ref,
        case_id="case-sq4c-read-only-perf",
        alert_id="alert-sq4c-read-only-perf",
        risk_id="risk-sq4c-read-only-perf",
        profile_id="profile-sq4c-read-only-perf",
    )
    db.commit()

    before_app = dict(db.execute(
        "SELECT ref, status, prescreening_data FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone())
    before_jobs = db.execute(
        "SELECT COUNT(*) AS c FROM screening_jobs WHERE application_id = ?",
        (app_id,),
    ).fetchone()["c"]

    def fail_provider_call(*args, **kwargs):
        raise AssertionError("screening queue read must not call provider screening")

    monkeypatch.setattr(server, "run_full_screening", fail_provider_call)
    monkeypatch.setattr(server, "screen_sumsub_aml", fail_provider_call)

    payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": app_ref},
        include_evidence=False,
    )

    assert any(row["application_ref"] == app_ref for row in payload["rows"])
    assert dict(db.execute(
        "SELECT ref, status, prescreening_data FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()) == before_app
    assert db.execute(
        "SELECT COUNT(*) AS c FROM screening_jobs WHERE application_id = ?",
        (app_id,),
    ).fetchone()["c"] == before_jobs


def test_screening_queue_universal_search_matches_application_subject_company_and_mesh_refs(db, temp_db):
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(
        db,
        app_id="app_sq4c_search",
        ref="ARF-SQ4C-SEARCH",
        subject_name="Universal Search Director",
        case_id="case-sq4c-search",
        alert_id="alert-sq4c-search",
        risk_id="risk-sq4c-search",
        profile_id="profile-sq4c-search",
    )
    _insert_sq2_ca_evidence(
        db,
        monitoring_id=19402,
        app_id="app_sq4c_search",
        case_id="case-sq4c-search",
        alert_id="alert-sq4c-search",
        matched_subject="Universal Search Director",
        risk_id="risk-sq4c-search",
        profile_id="profile-sq4c-search",
        evidence_hash="hash-sq4c-search",
    )
    db.commit()

    user = {"type": "officer", "sub": "admin001"}
    for search_term in (
        "ARF-SQ4C-SEARCH",
        "Universal Search Director",
        "SQ2 Evidence Ltd",
        "case-sq4c-search",
        "alert-sq4c-search",
        "risk-sq4c-search",
        "profile-sq4c-search",
    ):
        payload = _build_screening_queue_payload(
            db,
            user,
            filters={"search": search_term},
            include_evidence=False,
        )
        assert any(
            row["application_ref"] == "ARF-SQ4C-SEARCH" and row["subject_name"] == "Universal Search Director"
            for row in payload["rows"]
        ), search_term


def test_screening_queue_entity_pending_uses_broad_aml_wording(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_sq4c_pending_entity",
            "ARF-SQ4C-PENDING-ENTITY",
            "client_sq4c",
            "Pending Entity AML Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-06-01T00:00:00Z",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True,
                        "source": "complyadvantage",
                        "provider": "complyadvantage",
                        "sanctions": {
                            "matched": False,
                            "results": [],
                            "source": "complyadvantage",
                            "provider": "complyadvantage",
                            "api_status": "pending",
                        },
                    },
                    "director_screenings": [],
                    "ubo_screenings": [],
                    "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                    "kyc_applicants": [],
                    "overall_flags": [],
                    "total_hits": 0,
                }
            }),
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": "ARF-SQ4C-PENDING-ENTITY"},
    )
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ4C-PENDING-ENTITY" and r["subject_type"] == "entity")

    assert "Entity AML screening pending" in row["entity_context"]
    assert all("Company sanctions screening" not in item for item in row["entity_context"])


def test_screening_queue_available_type_filters_label_uncategorized_people_as_other_person():
    from server import _screening_queue_available_type_filters

    filters = _screening_queue_available_type_filters([
        {"subject_type": "entity"},
        {"subject_type": "director"},
        {"subject_type": "person"},
    ])

    assert {"value": "individual", "label": "Other person"} in filters


def test_screening_queue_summary_payload_respects_limit_and_offset(db, temp_db):
    from server import _build_screening_queue_payload

    for idx in range(5):
        _insert_sq2_screened_director(
            db,
            app_id=f"app_sq4c_page_{idx}",
            ref=f"ARF-SQ4C-PAGE-{idx}",
            subject_name=f"Paged Director {idx}",
            case_id=f"case-sq4c-page-{idx}",
            alert_id=f"alert-sq4c-page-{idx}",
            risk_id=f"risk-sq4c-page-{idx}",
            profile_id=f"profile-sq4c-page-{idx}",
        )
    db.commit()

    payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        filters={"search": "ARF-SQ4C-PAGE"},
        limit=2,
        offset=2,
        include_evidence=False,
    )

    assert payload["pagination"]["limit"] == 2
    assert payload["pagination"]["offset"] == 2
    assert payload["pagination"]["returned"] == 2
    assert payload["pagination"]["total_rows"] == 10
    assert payload["pagination"]["has_next"] is True
    assert payload["pagination"]["has_prev"] is True
    assert len(payload["rows"]) == 2
    assert all("provider_evidence" not in row for row in payload["rows"])


def test_screening_queue_links_ca_evidence_by_exact_identifiers(db, temp_db):
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(db, app_id="app_sq2_exact", ref="ARF-SQ2-EXACT")
    _insert_sq2_ca_evidence(db, monitoring_id=19301, app_id="app_sq2_exact")
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ2-EXACT" and r["subject_type"] == "director")

    assert row["status_key"] == "review_required"
    assert row["status_label"] == "Review Required"
    assert "raw_status" in row
    assert row["screening_evidence"]["evidence_status"] == "available"
    assert row["screening_evidence"]["evidence_quality"] == "complete"
    assert row["screening_evidence"]["evidence_quality_label"] == "Complete"
    assert row["screening_evidence"]["technical_details"]["linked_ca_1b_evidence_count"] == 1
    refs = row["screening_evidence"]["provider_references"]
    assert refs["provider"] == "complyadvantage"
    assert refs["provider_display_name"] == "ComplyAdvantage Mesh"
    assert refs["case_ids"] == ["case-sq2"]
    assert refs["alert_ids"] == ["alert-sq2"]
    assert refs["risk_ids"] == ["risk-sq2"]
    assert refs["profile_ids"] == ["profile-sq2"]
    evidence = row["screening_evidence"]["items"][0]
    assert evidence["source_title"] == "Provider article title"
    assert evidence["source_name"] == "Provider News"
    assert evidence["provider_case_id"] == "case-sq2"
    assert evidence["provider_alert_id"] == "alert-sq2"
    assert evidence["provider_risk_id"] == "risk-sq2"
    assert evidence["match_score"] == "0.92"
    assert evidence["source_url"] == ""
    assert evidence["source_url_unavailable_message"] == "Source unavailable from provider payload — verify in Mesh or attach supporting evidence."
    assert evidence["linking_method"] == "exact_identifier"
    assert row["current_risk_count"] == 1
    assert row["current_unresolved_risk_count"] == 1
    assert row["has_adverse_media_hit"] is True
    assert row["screening_evidence"]["evidence_quality_reason"] == "Evidence linked from CA provider evidence."
    diagnostics = row["screening_evidence"]["technical_details"]["diagnostics"]
    assert diagnostics["provider"] == "complyadvantage"
    assert diagnostics["identifier_presence"]["case"] is True
    assert diagnostics["field_presence"]["source_title"] is True


def test_screening_queue_preserves_nested_provider_references():
    from server import _enrich_screening_queue_evidence

    row = _enrich_screening_queue_evidence(
        {
            "application_id": "app_nested_refs",
            "application_ref": "ARF-NESTED-REFS",
            "company_name": "Nested Ref Ltd",
            "subject_name": "Nested Ref Ltd",
            "subject_type": "entity",
            "status_key": "review_required",
            "status_label": "Review Required",
            "total_hits": 1,
            "provider_evidence": [
                {
                    "provider": "complyadvantage",
                    "match_category": "Adverse Media",
                    "source_name": "Mesh Source",
                    "media_title": "Mesh article",
                    "media_url": "https://mesh.example.test/article",
                    "match_confidence": "0.88",
                    "provider_references": {
                        "case_ids": ["case-nested"],
                        "customer_ids": ["customer-nested"],
                        "workflow_ids": ["workflow-nested"],
                        "alert_ids": ["alert-nested"],
                        "risk_ids": ["risk-nested"],
                        "profile_ids": ["profile-nested"],
                        "provider_timestamp": "2026-06-01T10:00:00Z",
                    },
                }
            ],
        },
        [],
    )

    evidence = row["screening_evidence"]["items"][0]
    assert row["screening_evidence"]["evidence_quality"] == "complete"
    assert evidence["provider_case_id"] == "case-nested"
    assert evidence["provider_alert_id"] == "alert-nested"
    assert evidence["provider_risk_id"] == "risk-nested"
    assert evidence["provider_profile_id"] == "profile-nested"
    assert evidence["provider_customer_id"] == "customer-nested"
    assert evidence["provider_workflow_id"] == "workflow-nested"
    assert evidence["provider_timestamp"] == "2026-06-01T10:00:00Z"
    refs = row["screening_evidence"]["provider_references"]
    assert refs["customer_ids"] == ["customer-nested"]
    assert refs["workflow_ids"] == ["workflow-nested"]


def test_screening_queue_rolls_up_current_duplicate_stale_and_historical_risks():
    from server import _enrich_screening_queue_evidence

    row = _enrich_screening_queue_evidence(
        {
            "application_id": "app_rollup",
            "application_ref": "ARF-ROLLUP",
            "company_name": "Rollup Ltd",
            "subject_name": "Rollup Ltd",
            "subject_type": "entity",
            "status_key": "review_required",
            "status_label": "Review Required",
            "review_required": True,
            "total_hits": 4,
            "provider_evidence": [
                {
                    "provider": "complyadvantage",
                    "provider_risk_identifier": "risk-current-media",
                    "match_category": "Adverse Media",
                    "media_title": "Current media",
                    "summary": "Current adverse-media risk",
                },
                {
                    "provider": "complyadvantage",
                    "provider_risk_identifier": "risk-current-media",
                    "match_category": "Adverse Media",
                    "media_title": "Current media duplicate",
                    "summary": "Duplicate provider record",
                },
                {
                    "provider": "complyadvantage",
                    "provider_risk_identifier": "risk-stale",
                    "match_category": "Sanctions",
                    "provider_status": "stale",
                    "summary": "Stale risk",
                },
                {
                    "provider": "complyadvantage",
                    "provider_risk_identifier": "risk-historical",
                    "match_category": "PEP",
                    "risk_status": "archived",
                    "summary": "Historical risk",
                },
                {
                    "provider": "complyadvantage",
                    "provider_risk_identifier": "risk-cleared",
                    "match_category": "Sanctions",
                    "provider_decision": "false_positive",
                    "summary": "Provider-cleared risk",
                },
            ],
        },
        [],
    )

    assert row["current_risk_count"] == 1
    assert row["current_unresolved_risk_count"] == 1
    assert row["stale_risk_count"] == 1
    assert row["historical_risk_count"] == 2
    assert row["duplicate_provider_record_count"] == 1
    assert row["has_adverse_media_hit"] is True
    assert row["evidence_summary"]["risk_category_counts"] == {"Adverse Media": 1}


def test_screening_queue_does_not_attach_mismatched_ca_subject(db, temp_db):
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(
        db,
        app_id="app_sq2_mismatch",
        ref="ARF-SQ2-MISMATCH",
        case_id="case-sq2-mismatch",
        alert_id="alert-sq2-mismatch",
        risk_id="risk-sq2-mismatch",
        profile_id="profile-sq2-mismatch",
    )
    _insert_sq2_ca_evidence(
        db,
        monitoring_id=19302,
        app_id="app_sq2_mismatch",
        case_id="case-sq2-mismatch",
        alert_id="alert-sq2-mismatch",
        risk_id="risk-sq2-mismatch",
        profile_id="profile-sq2-mismatch",
        matched_subject="Different Director",
        title="Wrong subject article",
        evidence_hash="hash-sq2-mismatch",
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ2-MISMATCH" and r["subject_type"] == "director")

    titles = [item.get("source_title") for item in row["screening_evidence"]["items"]]
    assert "Wrong subject article" not in titles
    assert row["screening_evidence"]["technical_details"]["linked_ca_1b_evidence_count"] == 0
    assert row["screening_evidence"]["evidence_status"] == "partial"


def test_screening_queue_rejects_low_confidence_ca_evidence_without_exact_ids(db, temp_db):
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(db, app_id="app_sq2_low_conf", ref="ARF-SQ2-LOW-CONF")
    _insert_sq2_ca_evidence(
        db,
        monitoring_id=19303,
        app_id="app_sq2_low_conf",
        case_id="case-unrelated",
        alert_id="alert-unrelated",
        matched_subject="",
        risk_id="risk-unrelated",
        profile_id="profile-unrelated",
        title="Unrelated no subject article",
        evidence_hash="hash-sq2-low-conf",
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ2-LOW-CONF" and r["subject_type"] == "director")

    titles = [item.get("source_title") for item in row["screening_evidence"]["items"]]
    assert "Unrelated no subject article" not in titles
    assert row["screening_evidence"]["technical_details"]["linked_ca_1b_evidence_count"] == 0


def test_screening_queue_reports_structured_evidence_unavailable_honestly(db, temp_db):
    from server import _build_screening_queue_payload

    _insert_sq2_screened_director(
        db,
        app_id="app_sq2_unavailable",
        ref="ARF-SQ2-UNAVAILABLE",
        subject_name="Unavailable Evidence Director",
        risk_id="risk-sq2-unavailable",
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-SQ2-UNAVAILABLE" and r["subject_type"] == "director")

    assert row["screening_evidence"]["evidence_status"] == "partial"
    assert row["screening_evidence"]["items"][0]["source_url_unavailable_message"] == "Source unavailable from provider payload — verify in Mesh or attach supporting evidence."
    assert row["evidence_summary"]["partial_evidence_message"] == "Detailed provider evidence is partial or unavailable for this screening result."
    assert row["status_key"] == "review_required"


def test_screening_queue_failed_row_without_evidence_fetch_is_unavailable_not_failed():
    from server import _enrich_screening_queue_evidence

    row = _enrich_screening_queue_evidence(
        {
            "application_id": "app_failed_provider",
            "application_ref": "ARF-SQ3-FAILED-PROVIDER",
            "company_name": "Failed Provider Ltd",
            "subject_name": "Failed Subject",
            "subject_type": "director",
            "status_key": "failed",
            "status_label": "Failed",
            "screening_state": "failed",
            "screening_result": "failed",
            "total_hits": 0,
            "provider_evidence": [],
        },
        [],
    )

    assert row["screening_evidence"]["evidence_status"] == "unavailable"
    assert row["screening_evidence"]["evidence_failure_reason"] == "screening_failed_before_evidence"
    assert row["screening_evidence"]["evidence_quality_reason"] == "Provider screening failed before detailed evidence was available."
    assert row["screening_evidence"]["technical_details"]["diagnostics"]["failure_reason"] == "screening_failed_before_evidence"


def test_screening_queue_evidence_diagnostics_explain_missing_provider_identifiers():
    from server import _enrich_screening_queue_evidence

    row = _enrich_screening_queue_evidence(
        {
            "application_id": "app_missing_ids",
            "application_ref": "ARF-SQ3-MISSING-IDS",
            "company_name": "Missing IDs Ltd",
            "subject_name": "Missing IDs Ltd",
            "subject_type": "entity",
            "status_key": "review_required",
            "status_label": "Review Required",
            "total_hits": 1,
            "provider_evidence": [
                {
                    "provider": "complyadvantage",
                    "match_category": "Adverse Media",
                    "summary": "Provider shell without identifiers",
                }
            ],
        },
        [],
    )

    diagnostics = row["screening_evidence"]["technical_details"]["diagnostics"]
    assert row["screening_evidence"]["evidence_failure_reason"] == "missing_provider_identifiers"
    assert row["screening_evidence"]["evidence_quality_reason"] == "Missing provider identifiers."
    assert diagnostics["missing_identifier_types"] == ["alert", "case", "match", "profile", "risk"]
    assert diagnostics["field_presence"]["snippet"] is True
    assert row["screening_evidence"]["items"][0]["linking_method"] == "unavailable"


def test_screening_queue_monitoring_candidate_extracts_fields_from_evidence_json():
    from server import _monitoring_evidence_to_candidate

    candidate = _monitoring_evidence_to_candidate({
        "provider": "complyadvantage",
        "case_identifier": "case-json",
        "alert_identifier": "alert-json",
        "risk_identifier": "risk-json",
        "profile_identifier": "profile-json",
        "evidence_type": "adverse_media",
        "evidence_json": {
            "indicator": {
                "value": {
                    "title": "Evidence JSON title",
                    "canonical_url": {"url": "https://evidence.example.test/article"},
                    "publication_date": "2026-06-01",
                    "snippets": [{"text": "Evidence JSON snippet"}],
                    "source_metadata": {"source_identifier": "SRC-JSON"},
                }
            }
        },
        "raw_provider_reference": {},
        "alert_source_reference_json": {},
    })

    assert candidate["source_title"] == "Evidence JSON title"
    assert candidate["source_name"] == "SRC-JSON"
    assert candidate["source_url"] == "https://evidence.example.test/article"
    assert candidate["publication_date"] == "2026-06-01"
    assert candidate["snippet"] == "Evidence JSON snippet"


def test_entity_sanctions_record_falls_back_to_top_level_when_sub_record_absent():
    """A clean CA entity payload may omit the sanctions sub-record.

    The entity queue must still read a terminal live answer rather than
    not_started (which would pin the entity on "Screening In Progress").
    """
    import server
    from screening_state import derive_screening_state, COMPLETED_CLEAR, NOT_STARTED

    # Present sanctions sub-record → returned unchanged.
    present = {"sanctions": {"api_status": "live", "matched": False, "results": []}}
    assert server._entity_sanctions_record(present) == present["sanctions"]

    # Absent sanctions but live top-level record → fall back to top-level.
    absent = {"api_status": "live", "matched": False, "results": []}
    assert derive_screening_state(server._entity_sanctions_record(absent)) == COMPLETED_CLEAR

    # Genuinely empty → not_started (unchanged behaviour).
    assert derive_screening_state(server._entity_sanctions_record({})) == NOT_STARTED


def test_clean_ca_entity_report_resolves_terminal_clear_not_in_progress():
    """End-to-end: a clean CA entity resolves to a terminal clear queue state."""
    from screening_complyadvantage.normalizer import _empty_company_screening
    from screening_state import (
        derive_screening_state,
        resolve_screening_queue_state,
        COMPLETED_CLEAR,
    )
    import server

    company_screening = _empty_company_screening(
        screened_at="2026-01-01T00:00:00Z"
    )["company_screening"]
    company_sanctions = server._entity_sanctions_record(company_screening)
    company_state = derive_screening_state(company_sanctions)
    assert company_state == COMPLETED_CLEAR

    resolved = resolve_screening_queue_state({
        "subject_type": "entity",
        "status_key": "screened_no_match",
        "status_label": "No Match",
        "screening_state": company_state,
        "screening_truth_state": company_state,
        "provider_mode": "live_provider",
        "provider_availability": "available",
        "screening_result": "clear",
        "terminal": True,
        "defensible_clear": True,
        "total_hits": 0,
    })
    assert resolved["status_key"] != "screening_in_progress"
    assert resolved["terminal"] is True
    assert resolved["defensible_clear"] is True


def test_entity_provider_mode_record_fail_closed_selection():
    """The entity mode record must be a real record chosen fail-closed."""
    import server

    live_sanctions = {"api_status": "live", "source": "complyadvantage"}
    live_company = {"api_status": "live", "source": "complyadvantage", "matched": False, "results": []}
    pending_sanctions = {"api_status": "pending", "source": "complyadvantage"}
    simulated_company = {"api_status": "simulated", "source": "simulated"}

    # Both live -> sanctions record wins ties (entity AML source of truth).
    assert server._entity_provider_mode_record(live_company, live_sanctions) is live_sanctions
    # A pending sub-record beats a live sibling (fail-closed).
    assert server._entity_provider_mode_record(live_company, pending_sanctions) is pending_sanctions
    # A simulated top-level record beats a live sanctions record.
    assert server._entity_provider_mode_record(simulated_company, live_sanctions) is simulated_company
    # Guard aliasing (_entity_sanctions_record fallback): same object counted once.
    assert server._entity_provider_mode_record(live_company, live_company) is live_company
    # Nothing available -> empty record, never a synthetic one.
    assert server._entity_provider_mode_record({}, {}) == {}
    assert server._entity_provider_mode_record(None, None) == {}


def test_entity_row_mode_two_live_records_is_live_not_pending():
    """Regression: false "Screening Pending — Blocks Approval" badge.

    The queue previously space-joined api_status from the company record and
    its sanctions sub-record. Two live records produced api_status="live live",
    which matched no provider-mode token and fell through to pending, so a
    terminal-clear entity row rendered "Screening Pending — Blocks Approval".
    """
    import server
    from screening_state import provider_mode_from_record

    # The old joined pseudo-record demonstrates the trap this fix removes.
    assert provider_mode_from_record({"api_status": "live live"}) == "pending"

    company = {"api_status": "live", "source": "complyadvantage", "matched": False, "results": []}
    sanctions = {"api_status": "live", "source": "complyadvantage", "matched": False, "results": []}
    record = server._entity_provider_mode_record(company, sanctions)
    assert provider_mode_from_record(record) == "live_provider"
    mode = server._screening_queue_row_mode(
        "live", "completed_clear", "screened_no_match", ["Registry found"], record
    )
    assert mode == "live"

    # A genuinely pending sub-record must still surface as pending.
    pending = {"api_status": "pending", "source": "complyadvantage"}
    record = server._entity_provider_mode_record(company, pending)
    mode = server._screening_queue_row_mode(
        "live", "pending_provider", "screening_pending", [], record
    )
    assert mode == "pending"

    # Sandbox must still block honestly.
    sandbox = {"api_status": "sandbox", "source": "complyadvantage"}
    record = server._entity_provider_mode_record(company, sandbox)
    mode = server._screening_queue_row_mode(
        "live", "pending_provider", "screening_pending", [], record
    )
    assert mode == "sandbox"


def test_clean_ca_entity_row_mode_is_live_end_to_end():
    """Clean CA entity (normalizer shape + sanctions guard) renders a live badge."""
    from screening_complyadvantage.normalizer import _empty_company_screening
    import server

    company_screening = _empty_company_screening(
        screened_at="2026-01-01T00:00:00Z"
    )["company_screening"]
    company_sanctions = server._entity_sanctions_record(company_screening)
    record = server._entity_provider_mode_record(company_screening, company_sanctions)
    mode = server._screening_queue_row_mode(
        "live", "completed_clear", "screened_no_match", [], record
    )
    assert mode == "live"
