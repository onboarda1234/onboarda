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
    assert row["review_required"] is False
    assert row["review_actionable"] is False
    assert row["status_key"] == "cleared_by_officer"
    assert row["status_label"] == "Cleared by Officer"


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


def test_false_positive_clearance_queue_label_is_reviewed_not_raw_clear(db, temp_db):
    from server import _build_screening_queue_payload, upsert_screening_review

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_reviewed_fp_label",
            "ARF-REVIEWED-FP-LABEL",
            "client_reviewed_fp",
            "Reviewed FP Co",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-01-03T00:00:00",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True,
                        "sanctions": {
                            "matched": True,
                            "results": [{"name": "Reviewed FP Co", "is_sanctioned": True}],
                            "source": "sumsub",
                            "api_status": "live",
                        },
                    },
                    "director_screenings": [],
                    "ubo_screenings": [],
                }
            }),
        ),
    )
    db.commit()

    upsert_screening_review(
        db,
        "app_reviewed_fp_label",
        "entity",
        "Reviewed FP Co",
        "cleared",
        "Provider case CA-FP-LABEL-001 and registry evidence retained.",
        "co001",
        "Compliance Officer",
        disposition_code="false_positive_cleared",
        rationale="Officer confirmed the provider hit belongs to a different entity after registry comparison.",
        requires_four_eyes=False,
    )
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "co001",
            "Compliance Officer",
            "co",
            "Screening Review",
            "ARF-REVIEWED-FP-LABEL",
            json.dumps({
                "subject_type": "entity",
                "subject_name": "Reviewed FP Co",
                "disposition": "cleared",
                "disposition_code": "false_positive_cleared",
                "evidence_reference": "Provider case CA-FP-LABEL-001 and registry evidence retained.",
            }, sort_keys=True),
            "127.0.0.1",
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-REVIEWED-FP-LABEL")

    assert row["screening_state"] == "completed_match"
    assert row["status_key"] == "cleared_by_officer"
    assert row["status_label"] == "Cleared by Officer"
    assert row["review_resolved"] is True
    assert row["canonical_disposition"] == "false_positive_cleared"
    assert row["defensible_clear"] is True


def test_confirmed_match_queue_label_remains_blocking(db, temp_db):
    from server import _build_screening_queue_payload, upsert_screening_review

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_confirmed_match_label",
            "ARF-CONFIRMED-MATCH",
            "client_confirmed_match",
            "Confirmed Match Co",
            "Mauritius",
            "Technology",
            "SME",
            "edd_required",
            json.dumps({
                "screening_report": {
                    "screened_at": "2026-01-03T00:00:00",
                    "screening_mode": "live",
                    "company_screening": {
                        "found": True,
                        "sanctions": {
                            "matched": True,
                            "results": [{"name": "Confirmed Match Co", "is_sanctioned": True}],
                            "source": "sumsub",
                            "api_status": "live",
                        },
                    },
                    "director_screenings": [],
                    "ubo_screenings": [],
                }
            }),
        ),
    )
    db.commit()

    upsert_screening_review(
        db,
        "app_confirmed_match_label",
        "entity",
        "Confirmed Match Co",
        "escalated",
        "Officer confirmed the provider hit is relevant to this subject.",
        "co001",
        "Compliance Officer",
        disposition_code="confirmed_match",
        rationale="Officer confirmed the provider hit is relevant to this subject.",
        requires_four_eyes=False,
    )
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "co001",
            "Compliance Officer",
            "co",
            "Screening Review",
            "ARF-CONFIRMED-MATCH",
            json.dumps({
                "subject_type": "entity",
                "subject_name": "Confirmed Match Co",
                "disposition": "escalated",
                "disposition_code": "confirmed_match",
                "canonical_disposition": "confirmed_match",
            }, sort_keys=True),
            "127.0.0.1",
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-CONFIRMED-MATCH")

    assert row["status_key"] == "escalated"
    assert row["status_label"] == "Escalated"
    assert row["canonical_disposition"] == "confirmed_match"
    assert row["review_required"] is False


def test_screening_queue_search_filters_before_pagination(db, temp_db):
    from server import _build_screening_queue_payload

    for idx, (app_id, ref, company, provider) in enumerate([
        ("app_queue_filter_alpha", "ARF-FILTER-ALPHA", "Alpha Screening Ltd", "sumsub"),
        ("app_queue_filter_beta", "ARF-FILTER-BETA", "Beta Screening Ltd", "complyadvantage"),
    ]):
        db.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))
            """,
            (
                app_id,
                ref,
                f"client_filter_{idx}",
                company,
                "Mauritius",
                "Technology",
                "SME",
                "in_review",
                json.dumps({
                    "screening_report": {
                        "screened_at": "2026-01-03T00:00:00",
                        "screening_mode": "live",
                        "company_screening": {
                            "found": True,
                            "source": provider,
                            "provider": provider,
                            "sanctions": {
                                "matched": True,
                                "results": [{"name": company, "provider": provider, "is_sanctioned": True}],
                                "source": provider,
                                "provider": provider,
                                "api_status": "live",
                            },
                        },
                        "director_screenings": [],
                        "ubo_screenings": [],
                    }
                }),
                f"+{idx} minutes",
            ),
        )
    db.commit()

    payload = _build_screening_queue_payload(
        db,
        {"type": "officer", "sub": "admin001"},
        limit=1,
        offset=0,
        filters={"search": "Alpha", "provider": "sumsub", "status": "review_required"},
    )

    assert payload["pagination"]["total_rows"] == 1
    assert payload["pagination"]["returned"] == 1
    assert payload["rows"][0]["application_ref"] == "ARF-FILTER-ALPHA"
    assert payload["metrics"]["filtered_subject_rows"] == 1


def test_company_media_monitoring_alert_drives_screening_review_summary(db, temp_db):
    from server import _build_screening_queue_payload, _load_application_monitoring_alerts

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_ca_media_review",
            "ARF-CA-MEDIA",
            "client_ca_media",
            "CA Media Review Ltd",
            "Singapore",
            "Construction",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "provider": "complyadvantage",
                    "screened_at": "2026-05-11T02:04:41",
                    "screening_mode": "live",
                    "company_screening": {
                        "provider": "complyadvantage",
                        "source": "complyadvantage",
                        "api_status": "live",
                        "screened_at": "2026-05-11T02:04:41Z",
                        "matched": False,
                        "results": [],
                        "sanctions": {
                            "matched": False,
                            "results": [],
                            "source": "complyadvantage",
                            "api_status": "live",
                        },
                        "adverse_media": {
                            "matched": False,
                            "results": [],
                            "source": "complyadvantage",
                            "api_status": "live",
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
    db.execute(
        """
        INSERT INTO monitoring_alerts
        (application_id, client_name, alert_type, severity, detected_by, summary,
         source_reference, status, provider, case_identifier, discovered_via, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_ca_media_review",
            "CA Media Review Ltd",
            "media",
            "medium",
            "complyadvantage",
            "Company adverse media surfaced by ComplyAdvantage",
            json.dumps({
                "provider": "complyadvantage",
                "subject_type": "entity",
                "case_identifier": "case-company-media-1",
                "alert_identifier": "alert-company-media-1",
                "risk_identifier": "risk-company-media-1",
                "media_title": "Synthetic infrastructure enforcement article",
                "media_url": "https://example.test/company-media",
                "media_snippet": "Synthetic adverse-media snippet for officer review.",
            }),
            "open",
            "complyadvantage",
            "case-company-media-1",
            "webhook_backfill",
            "2026-05-11T02:04:57Z",
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-CA-MEDIA" and r["subject_type"] == "entity")

    assert row["status_key"] == "review_required"
    assert row["status_label"] == "Review Required"
    assert row["watchlist_status"] == "match"
    assert row["review_required"] is True
    assert row["total_hits"] == 1
    assert "Company adverse media match" in row["entity_context"]
    assert row["provider_evidence"]
    evidence = row["provider_evidence"][0]
    assert evidence["provider"] == "complyadvantage"
    assert evidence["provider_case_identifier"] == "case-company-media-1"
    assert evidence["provider_alert_identifier"] == "alert-company-media-1"
    assert evidence["provider_risk_identifier"] == "risk-company-media-1"
    assert evidence["subject_scope"] == "entity"
    assert evidence["match_categories"] == ["adverse media"]
    assert evidence["media_title"] == "Synthetic infrastructure enforcement article"
    assert evidence["media_url"] == "https://example.test/company-media"
    assert evidence["media_snippet"] == "Synthetic adverse-media snippet for officer review."

    alerts = _load_application_monitoring_alerts(db, "app_ca_media_review")
    assert alerts[0]["subject_scope"] == "entity"
    assert alerts[0]["source_reference_json"]["alert_identifier"] == "alert-company-media-1"
    assert alerts[0]["source_reference_json"]["risk_identifier"] == "risk-company-media-1"


def test_person_media_monitoring_alert_does_not_drive_company_summary(db, temp_db):
    from server import _build_screening_queue_payload, _load_application_monitoring_alerts

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_ca_person_media_only",
            "ARF-CA-PERSON-MEDIA",
            "client_ca_person_media",
            "Person Media Only Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "provider": "complyadvantage",
                    "screened_at": "2026-05-11T02:04:41",
                    "screening_mode": "live",
                    "company_screening": {
                        "provider": "complyadvantage",
                        "source": "complyadvantage",
                        "api_status": "live",
                        "screened_at": "2026-05-11T02:04:41Z",
                        "found": True,
                        "matched": False,
                        "results": [],
                        "sanctions": {
                            "matched": False,
                            "results": [],
                            "source": "complyadvantage",
                            "api_status": "live",
                        },
                        "adverse_media": {
                            "matched": False,
                            "results": [],
                            "source": "complyadvantage",
                            "api_status": "live",
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
    db.execute(
        """
        INSERT INTO monitoring_alerts
        (application_id, client_name, alert_type, severity, detected_by, summary,
         source_reference, status, provider, case_identifier, discovered_via, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_ca_person_media_only",
            "Person Media Only Ltd",
            "media",
            "medium",
            "complyadvantage",
            "Person-level adverse media surfaced by ComplyAdvantage",
            json.dumps({
                "provider": "complyadvantage",
                "subject_type": "director",
                "person_key": "person-donald-trump",
                "case_identifier": "case-person-media-1",
                "alert_identifier": "alert-person-media-1",
                "risk_identifier": "risk-person-media-1",
            }),
            "open",
            "complyadvantage",
            "case-person-media-1",
            "webhook_backfill",
            "2026-05-11T02:04:57Z",
        ),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-CA-PERSON-MEDIA" and r["subject_type"] == "entity")

    assert row["status_key"] == "clear"
    assert row["status_label"] == "Clear"
    assert row["watchlist_status"] == "clear"
    assert row["review_required"] is False
    assert row["total_hits"] == 0
    assert "Company adverse media match" not in row["entity_context"]

    alerts = _load_application_monitoring_alerts(db, "app_ca_person_media_only")
    assert alerts[0]["subject_scope"] == "person"


def test_undeclared_pep_queue_preserves_declaration_and_last_screened(db, temp_db):
    from server import _build_screening_queue_payload

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "app_ca_undeclared_pep_review",
            "ARF-CA-UNDECLARED",
            "client_ca_pep",
            "CA PEP Review Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "pricing_review",
            json.dumps({
                "screening_report": {
                    "provider": "complyadvantage",
                    "screened_at": "2026-05-11T02:04:41",
                    "screening_mode": "live",
                    "company_screening": {
                        "source": "complyadvantage",
                        "api_status": "live",
                        "sanctions": {"matched": False, "results": [], "source": "complyadvantage", "api_status": "live"},
                    },
                    "director_screenings": [{
                        "person_name": "Provider PEP",
                        "person_type": "director",
                        "declared_pep": "No",
                        "provider_detected_pep": True,
                        "undeclared_pep": True,
                        "has_pep_hit": True,
                        "screening": {
                            "matched": True,
                            "source": "complyadvantage",
                            "api_status": "live",
                            "screened_at": "2026-05-11T02:04:52Z",
                            "results": [{
                                "name": "Provider PEP",
                                "is_pep": True,
                                "match_categories": ["PEP"],
                                "risk_type_labels": ["PEP class 1"],
                                "provider_risk_identifier": "risk-pep-1",
                                "provider_alert_identifier": "alert-pep-1",
                                "provider_case_identifier": "case-pep-1",
                                "provider_profile_identifier": "profile-pep-1",
                                "subject_scope": "person",
                            }],
                        },
                    }],
                    "ubo_screenings": [],
                    "ip_geolocation": {"risk_level": "LOW", "source": "ipapi"},
                    "kyc_applicants": [],
                    "overall_flags": ["ComplyAdvantage PEP hit: risk-pep-1"],
                    "total_hits": 1,
                }
            }),
        ),
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
        ("app_ca_undeclared_pep_review", "Provider PEP", "Mauritius", "Yes"),
    )
    db.commit()

    payload = _build_screening_queue_payload(db, {"type": "officer", "sub": "admin001"})
    row = next(r for r in payload["rows"] if r["application_ref"] == "ARF-CA-UNDECLARED" and r["subject_type"] == "director")

    assert row["pep_declared_status"] == "not_declared"
    assert row["pep_screening_status"] == "match"
    assert row["status_key"] == "review_required"
    assert "Undeclared PEP" in row["entity_context"]
    assert "Declared PEP" not in row["entity_context"]
    assert row["screened_at"] == "2026-05-11T02:04:52Z"
    assert row["provider_evidence"]
    evidence = row["provider_evidence"][0]
    assert evidence["matched_name"] == "Provider PEP"
    assert evidence["provider_case_identifier"] == "case-pep-1"
    assert evidence["provider_alert_identifier"] == "alert-pep-1"
    assert evidence["provider_risk_identifier"] == "risk-pep-1"
    assert evidence["provider_profile_identifier"] == "profile-pep-1"
    assert evidence["subject_scope"] == "person"
    assert evidence["match_categories"] == ["PEP"]
    assert evidence["risk_type_labels"] == ["PEP class 1"]
