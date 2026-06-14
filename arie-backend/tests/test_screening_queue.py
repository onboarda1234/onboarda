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
    assert evidence["source_url_unavailable_message"] == "Source link not available from provider payload."
    assert evidence["linking_method"] == "exact_identifier"
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
    assert row["screening_evidence"]["items"][0]["source_url_unavailable_message"] == "Source link not available from provider payload."
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
