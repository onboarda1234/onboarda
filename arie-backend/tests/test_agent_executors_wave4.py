"""
Wave 4 — Agents 6, 7, 8, 10: Monitoring agent tests.

Covers all workbook-aligned checks:
  Agent 6:  10 checks (8 rule + 2 hybrid) — Periodic Review Preparation
  Agent 7:  12 checks (6 rule + 4 hybrid + 2 AI) — Adverse Media & PEP Monitoring
  Agent 8:  11 checks (6 rule + 5 hybrid) — Behaviour & Risk Drift
  Agent 10: 11 checks (7 rule + 2 hybrid + 2 AI) — Ongoing Compliance Review
"""
import os
import sys
import json
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from supervisor.agent_executors import (
    # Agent 6
    execute_periodic_review,
    _check_review_schedule,
    _check_risk_level_change,
    _check_document_expiry,
    _check_ownership_changes,
    _check_screening_staleness,
    _check_activity_volume,
    _check_outstanding_alerts,
    _check_regulatory_completeness,
    _compute_review_priority,
    _assemble_review_package,
    # Agent 8
    execute_behaviour_risk_drift,
    _check_volume_baseline,
    _check_geographic_deviation,
    _check_counterparty_concentration,
    _check_product_usage_deviation,
    _check_dormancy,
    _check_threshold_breach,
    _score_velocity_anomaly,
    _score_peer_deviation,
    _detect_temporal_drift,
    _compute_multi_dimensional_drift,
    _generate_drift_narrative,
    # Agent 7
    execute_adverse_media_pep,
    _retrieve_new_media,
    _detect_pep_changes,
    _check_sanctions_updates,
    _score_media_credibility,
    _deduplicate_alerts,
    _compare_historical_media,
    _assess_media_severity,
    _score_pep_proximity,
    _resolve_entities,
    _aggregate_risk_signals,
    _generate_media_narrative,
    _determine_monitoring_disposition,
    # Agent 10
    execute_ongoing_compliance,
    _check_document_currency,
    _check_screening_recency,
    _check_policy_applicability,
    _check_condition_compliance,
    _check_filing_deadlines,
    _consolidate_inter_agent_findings,
    _track_remediation,
    _rescore_compliance_risk,
    _recommend_review_frequency,
    _generate_compliance_narrative,
    _recommend_escalation_closure,
    # Monitoring helper
    _get_monitoring_data,
    # Constants
    _REVIEW_FREQUENCY_MAP,
    _SCREENING_STALENESS_DAYS,
    _DORMANCY_DAYS,
)


# ── Test DB fixture ──────────────────────────────────────────

def _create_test_db(app_overrides=None, add_alerts=False, add_reviews=False):
    """Create a minimal test SQLite DB with monitoring tables."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    db = sqlite3.connect(db_path)
    db.execute("""CREATE TABLE applications (
        id TEXT PRIMARY KEY, ref TEXT, company_name TEXT, country TEXT,
        registration_number TEXT, entity_type TEXT, ownership_structure TEXT,
        sector TEXT, risk_level TEXT, risk_score REAL, source_of_funds TEXT,
        expected_volume TEXT, client_id TEXT, status TEXT DEFAULT 'submitted',
        brn TEXT, assigned_to TEXT, prescreening_data TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE directors (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        position TEXT, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE ubos (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        ownership_pct REAL, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE intermediaries (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        entity_name TEXT, jurisdiction TEXT, ownership_pct REAL
    )""")
    db.execute("""CREATE TABLE documents (
        id TEXT PRIMARY KEY, application_id TEXT, document_type TEXT,
        filename TEXT, verification_status TEXT DEFAULT 'pending',
        expiry_date TEXT, valid_until TEXT
    )""")
    db.execute("""CREATE TABLE monitoring_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT,
        client_name TEXT, alert_type TEXT, severity TEXT, detected_by TEXT,
        summary TEXT, source_reference TEXT, ai_recommendation TEXT,
        status TEXT DEFAULT 'open', officer_action TEXT, officer_notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TIMESTAMP, reviewed_by TEXT
    )""")
    db.execute("""CREATE TABLE periodic_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT,
        client_name TEXT, risk_level TEXT, trigger_type TEXT, trigger_reason TEXT,
        previous_risk_level TEXT, new_risk_level TEXT, review_memo TEXT,
        status TEXT DEFAULT 'pending', due_date DATE,
        started_at TIMESTAMP, completed_at TIMESTAMP,
        decision TEXT, decision_reason TEXT, decided_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE monitoring_agent_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT, agent_type TEXT,
        last_run TIMESTAMP, next_run TIMESTAMP, run_frequency TEXT,
        clients_monitored INTEGER, alerts_generated INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active'
    )""")

    app_id = "wave4-test-001"
    app_defaults = {
        "id": app_id, "ref": "APP-W4-001", "company_name": "Wave4Test Ltd",
        "country": "United Kingdom", "entity_type": "Private Company Limited",
        "sector": "Technology", "risk_level": "MEDIUM", "risk_score": 35.0,
        "expected_volume": "50000", "status": "approved",
        "prescreening_data": "{}",
    }
    if app_overrides:
        app_defaults.update(app_overrides)

    cols = ", ".join(app_defaults.keys())
    placeholders = ", ".join(["?"] * len(app_defaults))
    db.execute(f"INSERT INTO applications ({cols}) VALUES ({placeholders})", tuple(app_defaults.values()))

    db.execute("INSERT INTO directors (id, application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?, ?)",
               ("dir-w4-1", app_id, "Alice Director", "UK", "No"))
    db.execute("INSERT INTO ubos (id, application_id, full_name, ownership_pct, nationality, is_pep) VALUES (?, ?, ?, ?, ?, ?)",
               ("ubo-w4-1", app_id, "Bob Owner", 60.0, "UK", "No"))
    db.execute("INSERT INTO documents (id, application_id, document_type, filename, verification_status) VALUES (?, ?, ?, ?, ?)",
               ("doc-w4-1", app_id, "passport", "passport.pdf", "verified"))
    db.execute("INSERT INTO documents (id, application_id, document_type, filename, verification_status) VALUES (?, ?, ?, ?, ?)",
               ("doc-w4-2", app_id, "cert_inc", "cert_inc.pdf", "verified"))

    if add_alerts:
        db.execute(
            "INSERT INTO monitoring_alerts (application_id, alert_type, severity, summary, status) VALUES (?, ?, ?, ?, ?)",
            (app_id, "adverse_media", "high", "Negative press article about Wave4Test", "open"))
        db.execute(
            "INSERT INTO monitoring_alerts (application_id, alert_type, severity, summary, status, ai_recommendation) VALUES (?, ?, ?, ?, ?, ?)",
            (app_id, "threshold_breach", "medium", "Volume threshold exceeded", "open", "Review transaction patterns"))

    if add_reviews:
        db.execute(
            "INSERT INTO periodic_reviews (application_id, risk_level, status, completed_at) VALUES (?, ?, ?, ?)",
            (app_id, "LOW", "completed", "2025-06-15T10:00:00"))

    db.commit()
    db.close()
    return db_path, app_id


# ═══════════════════════════════════════════════════════════
# AGENT 6: Periodic Review Preparation Tests
# ═══════════════════════════════════════════════════════════

class TestReviewSchedule:
    def test_no_prior_reviews(self):
        result = _check_review_schedule({"risk_level": "MEDIUM"}, [])
        assert result["schedule_status"] == "no_history"
        assert result["classification"] == "rule"

    def test_overdue_review(self):
        reviews = [{"completed_at": "2024-01-01T00:00:00"}]
        result = _check_review_schedule({"risk_level": "MEDIUM"}, reviews)
        assert result["schedule_status"] == "overdue"
        assert result["days_since_last_review"] > 365

    def test_on_schedule(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        reviews = [{"completed_at": recent}]
        result = _check_review_schedule({"risk_level": "LOW"}, reviews)
        assert result["schedule_status"] == "on_schedule"


class TestRiskLevelChange:
    def test_no_change(self):
        reviews = [{"risk_level": "MEDIUM"}]
        result = _check_risk_level_change({"risk_level": "MEDIUM"}, reviews)
        assert result["changed"] is False

    def test_change_detected(self):
        reviews = [{"risk_level": "LOW"}]
        result = _check_risk_level_change({"risk_level": "HIGH"}, reviews)
        assert result["changed"] is True

    def test_no_prior_reviews(self):
        result = _check_risk_level_change({"risk_level": "MEDIUM"}, [])
        assert result["status"] == "no_prior_review"


class TestDocumentExpiry:
    def test_no_expiry_dates(self):
        docs = [{"document_type": "passport", "id": "d1"}]
        result = _check_document_expiry(docs)
        assert result["expired_count"] == 0

    def test_expired_document(self):
        docs = [{"id": "d1", "document_type": "passport", "expiry_date": "2020-01-01T00:00:00"}]
        result = _check_document_expiry(docs)
        assert result["expired_count"] == 1
        assert len(result["expired_documents"]) == 1

    def test_expiring_soon(self):
        from datetime import datetime, timedelta, timezone
        soon = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        docs = [{"id": "d1", "document_type": "passport", "expiry_date": soon}]
        result = _check_document_expiry(docs)
        assert result["expiring_soon_count"] == 1


class TestOwnershipChanges:
    def test_no_intermediaries(self):
        result = _check_ownership_changes({}, [{"ownership_pct": 60}], [])
        assert result["changes_detected"] == 0

    def test_intermediary_flagged(self):
        result = _check_ownership_changes({}, [], [{"entity_name": "HoldCo"}])
        assert result["changes_detected"] >= 1

    def test_borderline_ubo(self):
        result = _check_ownership_changes({}, [{"full_name": "Bob", "ownership_pct": 25}], [])
        assert result["changes_detected"] >= 1


class TestScreeningStaleness:
    def test_no_screening_data(self):
        result = _check_screening_staleness({"prescreening_data": "{}"})
        assert result["is_stale"] is True

    def test_fresh_screening(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        data = json.dumps({"screening_report": {"screened_at": recent}})
        result = _check_screening_staleness({"prescreening_data": data})
        assert result["is_stale"] is False

    def test_stale_screening(self):
        data = json.dumps({"screening_report": {"screened_at": "2024-01-01T00:00:00"}})
        result = _check_screening_staleness({"prescreening_data": data})
        assert result["is_stale"] is True


class TestActivityVolume:
    def test_degraded_mode(self):
        result = _check_activity_volume({"expected_volume": "50000"})
        assert result["status"] == "completed" or result["data_available"] is False


class TestOutstandingAlerts:
    def test_no_alerts(self):
        result = _check_outstanding_alerts([])
        assert result["total_open_alerts"] == 0

    def test_open_alerts(self):
        alerts = [
            {"id": 1, "alert_type": "media", "severity": "high", "status": "open"},
            {"id": 2, "alert_type": "threshold", "severity": "medium", "status": "resolved"},
        ]
        result = _check_outstanding_alerts(alerts)
        assert result["total_open_alerts"] == 1


class TestRegulatoryCompleteness:
    def test_complete_set(self):
        docs = [
            {"document_type": "passport"},
            {"document_type": "poa"},
            {"document_type": "cert_inc"},
        ]
        result = _check_regulatory_completeness({"entity_type": "Individual"}, docs)
        assert result["completeness_pct"] == 100.0

    def test_missing_documents(self):
        docs = [{"document_type": "passport"}]
        result = _check_regulatory_completeness({"entity_type": "Individual"}, docs)
        assert result["completeness_pct"] < 100


class TestReviewPriority:
    def test_low_priority(self):
        checks = [
            {"check": "Review schedule", "schedule_status": "on_schedule"},
            {"check": "Risk level change", "changed": False},
            {"check": "Document expiry scan", "expired_count": 0},
        ]
        result = _compute_review_priority(checks)
        assert result["priority_label"] == "low"

    def test_high_priority(self):
        checks = [
            {"check": "Review schedule", "schedule_status": "overdue"},
            {"check": "Risk level change", "changed": True},
            {"check": "Document expiry scan", "expired_count": 3},
            {"check": "Ownership structure", "changes_detected": 1},
            {"check": "Screening staleness", "is_stale": True},
            {"check": "Outstanding alert aggregation", "total_open_alerts": 5},
        ]
        result = _compute_review_priority(checks)
        assert result["priority_label"] in ("high", "medium")
        assert result["priority_score"] >= 40


class TestReviewPackage:
    def test_assembly(self):
        checks = [{"check": "Document expiry scan", "expired_count": 2}]
        priority = {"priority_score": 45, "priority_label": "medium"}
        result = _assemble_review_package({"company_name": "Test", "risk_level": "MEDIUM"}, checks, priority)
        assert result["total_issues"] >= 1


class TestAgent6Integration:
    def test_full_execution(self):
        db_path, app_id = _create_test_db()
        result = execute_periodic_review(app_id, {"db_path": db_path})
        assert result["agent_type"] == "periodic_review_preparation"
        assert len(result["checks_performed"]) == 10
        assert result["review_trigger"] == "scheduled"
        assert result["priority_score"] is not None
        os.unlink(db_path)

    def test_with_alerts(self):
        db_path, app_id = _create_test_db(add_alerts=True)
        result = execute_periodic_review(app_id, {"db_path": db_path})
        assert len(result["outstanding_alerts"]) > 0
        os.unlink(db_path)

    def test_with_prior_review(self):
        db_path, app_id = _create_test_db(add_reviews=True)
        result = execute_periodic_review(app_id, {"db_path": db_path})
        assert result["checks_performed"][0]["check"] == "Review schedule compliance check"
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# AGENT 8: Behaviour & Risk Drift Tests
# ═══════════════════════════════════════════════════════════

class TestVolumeBaseline:
    def test_degraded_mode(self):
        result = _check_volume_baseline({"expected_volume": "50000"})
        assert result["status"] == "degraded"
        assert result["mode"] == "no_transaction_data"


class TestGeographicDeviation:
    def test_degraded_mode(self):
        result = _check_geographic_deviation({"country": "UK"})
        assert result["status"] == "degraded"
        assert result["declared_country"] == "UK"


class TestCounterpartyConcentration:
    def test_degraded_mode(self):
        result = _check_counterparty_concentration({})
        assert result["status"] == "degraded"


class TestProductUsageDeviation:
    def test_degraded_mode(self):
        result = _check_product_usage_deviation({"sector": "Technology"})
        assert result["declared_sector"] == "Technology"


class TestDormancy:
    def test_not_dormant(self):
        from datetime import datetime, timezone
        result = _check_dormancy({"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()})
        assert result["is_dormant"] is False

    def test_dormant(self):
        result = _check_dormancy({"status": "approved", "updated_at": "2024-01-01T00:00:00"})
        assert result["is_dormant"] is True


class TestThresholdBreach:
    def test_no_threshold_alerts(self):
        result = _check_threshold_breach({}, [{"alert_type": "adverse_media", "status": "open"}])
        assert result["threshold_breaches_found"] == 0

    def test_threshold_alert_found(self):
        alerts = [{"id": 1, "alert_type": "threshold_breach", "severity": "high", "summary": "Volume exceeded"}]
        result = _check_threshold_breach({}, alerts)
        assert result["threshold_breaches_found"] == 1


class TestVelocityAnomaly:
    def test_no_anomaly(self):
        checks = [{"is_dormant": False, "threshold_breaches_found": 0}]
        result = _score_velocity_anomaly(checks)
        assert result["anomaly_detected"] is False
        assert result["velocity_score"] == 0

    def test_dormancy_contributes(self):
        checks = [{"check": "Dormancy", "is_dormant": True}]
        result = _score_velocity_anomaly(checks)
        assert result["velocity_score"] > 0


class TestPeerDeviation:
    def test_degraded_mode(self):
        result = _score_peer_deviation({"sector": "Tech", "risk_level": "MEDIUM"})
        assert result["status"] == "degraded"


class TestTemporalDrift:
    def test_no_drift(self):
        checks = [{"check": "Dormancy/reactivation detection", "is_dormant": False, "days_since_last_activity": 30}]
        result = _detect_temporal_drift({}, checks)
        assert result["temporal_drift_detected"] is False

    def test_drift_detected(self):
        checks = [{"check": "Dormancy/reactivation detection", "is_dormant": True, "days_since_last_activity": 400}]
        result = _detect_temporal_drift({}, checks)
        assert result["temporal_drift_detected"] is True


class TestMultiDimensionalDrift:
    def test_zero_drift(self):
        result = _compute_multi_dimensional_drift([], [])
        assert result["drift_score"] == 0
        assert result["drift_detected"] is False

    def test_elevated_drift(self):
        rule = [{"check": "Dormancy/reactivation detection", "is_dormant": True}]
        hybrid = [{"check": "Velocity anomaly scoring", "velocity_score": 0.5}]
        result = _compute_multi_dimensional_drift(rule, hybrid)
        assert result["drift_score"] > 0


class TestDriftNarrative:
    def test_clean_narrative(self):
        drift = {"drift_score": 0, "drift_direction": "stable"}
        result = _generate_drift_narrative({"company_name": "Test"}, drift, [])
        assert "stable" in result["narrative"].lower() or "no significant" in result["narrative"].lower()
        assert result["recommendation"] == "continue_monitoring"

    def test_elevated_narrative(self):
        drift = {"drift_score": 0.6, "drift_direction": "increasing"}
        checks = [{"is_dormant": True, "anomaly_detected": True}]
        result = _generate_drift_narrative({"company_name": "Test"}, drift, checks)
        assert result["recommendation"] == "enhanced_due_diligence"


class TestAgent8Integration:
    def test_full_execution(self):
        db_path, app_id = _create_test_db()
        result = execute_behaviour_risk_drift(app_id, {"db_path": db_path})
        assert result["agent_type"] == "behaviour_risk_drift"
        assert len(result["checks_performed"]) == 11
        assert "drift_direction" in result
        assert "drift_magnitude" in result
        os.unlink(db_path)

    def test_with_threshold_alerts(self):
        db_path, app_id = _create_test_db(add_alerts=True)
        result = execute_behaviour_risk_drift(app_id, {"db_path": db_path})
        assert result["checks_performed"] is not None
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# AGENT 7: Adverse Media & PEP Monitoring Tests
# ═══════════════════════════════════════════════════════════

class TestNewMediaRetrieval:
    def test_no_media_alerts(self):
        result = _retrieve_new_media({}, [])
        assert result["media_alerts_found"] == 0

    def test_media_alerts_found(self):
        alerts = [
            {"id": 1, "alert_type": "adverse_media", "summary": "Bad press", "severity": "high", "source_reference": "major_news", "status": "open"},
        ]
        result = _retrieve_new_media({}, alerts)
        assert result["media_alerts_found"] == 1


class TestPepChanges:
    def test_no_peps(self):
        result = _detect_pep_changes({}, [{"is_pep": "No"}], [{"is_pep": "No"}])
        assert result["current_pep_count"] == 0

    def test_pep_detected(self):
        directors = [{"full_name": "PEP Director", "is_pep": "Yes"}]
        result = _detect_pep_changes({}, directors, [])
        assert result["current_pep_count"] == 1


class TestSanctionsUpdates:
    def test_no_sanctions(self):
        result = _check_sanctions_updates({}, [])
        assert result["sanctions_alerts_found"] == 0

    def test_sanctions_found(self):
        alerts = [{"id": 1, "alert_type": "sanctions", "summary": "Match", "severity": "critical"}]
        result = _check_sanctions_updates({}, alerts)
        assert result["sanctions_alerts_found"] == 1


class TestMediaCredibility:
    def test_scoring(self):
        media = {"hits": [
            {"alert_id": 1, "source": "government", "summary": "Alert"},
            {"alert_id": 2, "source": "social_media", "summary": "Rumour"},
        ]}
        result = _score_media_credibility(media)
        assert result["high_credibility_count"] == 1  # government >= 0.7


class TestAlertDeduplication:
    def test_no_duplicates(self):
        media = {"hits": [{"alert_id": 1}]}
        sanctions = {"hits": [{"alert_id": 2}]}
        result = _deduplicate_alerts(media, sanctions, {})
        assert result["duplicates_removed"] == 0

    def test_with_duplicates(self):
        media = {"hits": [{"alert_id": 1}, {"alert_id": 1}]}
        sanctions = {"hits": []}
        result = _deduplicate_alerts(media, sanctions, {})
        assert result["duplicates_removed"] >= 1


class TestHistoricalComparison:
    def test_no_baseline(self):
        result = _compare_historical_media({"prescreening_data": "{}"}, {"media_alerts_found": 2})
        assert result["has_baseline"] is False

    def test_with_baseline(self):
        data = json.dumps({"screening_report": {"adverse_media": {"hits": [{"name": "x"}]}}})
        result = _compare_historical_media({"prescreening_data": data}, {"media_alerts_found": 3})
        assert result["has_baseline"] is True
        assert result["new_since_baseline"] == 2


class TestMediaSeverity:
    def test_severity_scoring(self):
        media = {"media_alerts_found": 1}
        credibility = {"scored_hits": [{"summary": "sanctions violation", "credibility_score": 0.9}]}
        result = _assess_media_severity(media, credibility)
        assert result["max_adjusted_severity"] > 0


class TestPepProximity:
    def test_no_peps(self):
        result = _score_pep_proximity({"pep_persons": []}, [], [])
        assert result["max_proximity"] == 0

    def test_director_pep(self):
        pep_check = {"pep_persons": [{"name": "John", "role": "director"}]}
        result = _score_pep_proximity(pep_check, [], [])
        assert result["max_proximity"] > 0


class TestEntityResolution:
    def test_matched_entity(self):
        media = {"hits": [{"alert_id": 1, "summary": "Wave4Test Ltd in trouble"}]}
        result = _resolve_entities(media, {}, {"company_name": "Wave4Test Ltd"})
        assert result["resolved_count"] == 1

    def test_unresolved_entity(self):
        media = {"hits": [{"alert_id": 1, "summary": "Other Corp problems"}]}
        result = _resolve_entities(media, {}, {"company_name": "Wave4Test Ltd"})
        assert result["unresolved_count"] == 1


class TestRiskSignalAggregation:
    def test_clean_signals(self):
        checks = [
            {"check": "Media severity assessment", "max_adjusted_severity": 0},
            {"check": "PEP proximity scoring", "max_proximity": 0},
            {"check": "Sanctions list update check", "sanctions_alerts_found": 0},
        ]
        result = _aggregate_risk_signals(checks)
        assert result["risk_level"] == "low"

    def test_elevated_signals(self):
        checks = [
            {"check": "Media severity assessment", "max_adjusted_severity": 3.0},
            {"check": "PEP proximity scoring", "max_proximity": 0.8},
            {"check": "Sanctions list update check", "sanctions_alerts_found": 1},
        ]
        result = _aggregate_risk_signals(checks)
        assert result["risk_level"] in ("critical", "high")


class TestMediaNarrative:
    def test_clean_narrative(self):
        result = _generate_media_narrative({"company_name": "Test"}, [
            {"check": "New adverse media retrieval", "media_alerts_found": 0},
            {"check": "PEP status change detection", "current_pep_count": 0},
            {"check": "Sanctions list update check", "sanctions_alerts_found": 0},
        ])
        assert "clean" in result["narrative"].lower() or "no new" in result["narrative"].lower()

    def test_narrative_with_hits(self):
        result = _generate_media_narrative({"company_name": "Test"}, [
            {"check": "New adverse media retrieval", "media_alerts_found": 3},
            {"check": "PEP status change detection", "current_pep_count": 1},
            {"check": "Sanctions list update check", "sanctions_alerts_found": 0},
            {"check": "Combined risk signal aggregation", "risk_level": "medium"},
        ])
        assert "3" in result["narrative"] or "media" in result["narrative"].lower()


class TestMonitoringDisposition:
    def test_clear_disposition(self):
        result = _determine_monitoring_disposition(
            {"company_name": "Test"}, {"combined_score": 0, "risk_level": "low", "signals": {}}, [])
        assert result["disposition"] == "CLEAR"

    def test_escalate_disposition(self):
        result = _determine_monitoring_disposition(
            {"company_name": "Test"}, {"combined_score": 3.5, "risk_level": "critical", "signals": {}}, [])
        assert result["disposition"] == "ESCALATE"


class TestAgent7Integration:
    def test_full_execution_clean(self):
        db_path, app_id = _create_test_db()
        result = execute_adverse_media_pep(app_id, {"db_path": db_path})
        assert result["agent_type"] == "adverse_media_pep_monitoring"
        assert len(result["checks_performed"]) == 12
        assert result["alert_generated"] is False
        os.unlink(db_path)

    def test_with_media_alerts(self):
        db_path, app_id = _create_test_db(add_alerts=True)
        result = execute_adverse_media_pep(app_id, {"db_path": db_path})
        assert len(result["new_media_hits"]) > 0
        os.unlink(db_path)

    def test_with_pep_director(self):
        db_path, app_id = _create_test_db()
        # Add a PEP director
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO directors (id, application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?, ?)",
                   ("dir-pep-1", app_id, "PEP Director", "UK", "Yes"))
        db.commit()
        db.close()
        result = execute_adverse_media_pep(app_id, {"db_path": db_path})
        assert any("PEP" in f.get("title", "") for f in result["findings"])
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# AGENT 10: Ongoing Compliance Review Tests
# ═══════════════════════════════════════════════════════════

class TestDocumentCurrency:
    def test_all_current(self):
        docs = [{"id": "d1", "document_type": "passport", "verification_status": "verified"}]
        result = _check_document_currency(docs)
        assert result["expired_count"] == 0

    def test_expired_document(self):
        docs = [{"id": "d1", "document_type": "passport", "expiry_date": "2020-01-01T00:00:00", "verification_status": "verified"}]
        result = _check_document_currency(docs)
        assert result["expired_count"] == 1


class TestScreeningRecency:
    def test_no_screening(self):
        result = _check_screening_recency({"prescreening_data": "{}"})
        assert result["status"] == "no_screening_data"

    def test_recent_screening(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        data = json.dumps({"screening_report": {"screened_at": recent}})
        result = _check_screening_recency({"prescreening_data": data})
        assert result["is_recent"] is True


class TestPolicyApplicability:
    def test_no_triggers(self):
        result = _check_policy_applicability({"country": "United Kingdom", "sector": "Technology", "risk_level": "LOW"})
        assert result["trigger_count"] == 0

    def test_high_risk_jurisdiction(self):
        result = _check_policy_applicability({"country": "Iran", "sector": "Technology", "risk_level": "LOW"})
        assert result["trigger_count"] >= 1

    def test_high_risk_sector(self):
        result = _check_policy_applicability({"country": "UK", "sector": "Gambling", "risk_level": "LOW"})
        assert result["trigger_count"] >= 1


class TestConditionCompliance:
    def test_no_conditions(self):
        result = _check_condition_compliance({"status": "approved"}, [])
        assert result["conditions_unmet"] == 0

    def test_conditional_approval(self):
        result = _check_condition_compliance({"status": "conditionally_approved"}, [])
        assert result["conditions_unmet"] >= 1


class TestFilingDeadlines:
    def test_with_creation_date(self):
        result = _check_filing_deadlines({"created_at": "2025-01-01T00:00:00"})
        assert len(result["deadlines"]) > 0

    def test_no_date(self):
        result = _check_filing_deadlines({})
        assert result["status"] == "no_date_available"


class TestInterAgentConsolidation:
    def test_empty(self):
        result = _consolidate_inter_agent_findings([], [])
        assert result["total_alerts"] == 0

    def test_with_alerts(self):
        alerts = [
            {"alert_type": "media", "status": "open"},
            {"alert_type": "media", "status": "resolved"},
            {"alert_type": "threshold", "status": "open"},
        ]
        result = _consolidate_inter_agent_findings(alerts, [])
        assert result["open_alerts"] == 2
        assert result["by_alert_type"]["media"] == 2


class TestRemediationTracker:
    def test_no_remediation(self):
        result = _track_remediation([{"status": "resolved"}])
        assert result["open_remediation_items"] == 0

    def test_open_remediation(self):
        alerts = [{"status": "open", "ai_recommendation": "Review needed", "id": 1, "alert_type": "media", "severity": "high"}]
        result = _track_remediation(alerts)
        assert result["open_remediation_items"] == 1


class TestComplianceRiskScoring:
    def test_low_risk(self):
        checks = [
            {"check": "Document currency verification", "expired_count": 0},
            {"check": "Screening recency check", "is_recent": True},
            {"check": "Policy change applicability check", "trigger_count": 0},
            {"check": "Condition compliance tracking", "conditions_unmet": 0},
            {"check": "Filing deadline monitoring", "overdue_count": 0},
            {"check": "Inter-agent finding consolidation", "open_alerts": 0},
            {"check": "Remediation tracker status", "open_remediation_items": 0},
        ]
        result = _rescore_compliance_risk(checks, {"risk_level": "LOW"})
        assert result["compliance_risk_score"] <= 30

    def test_high_risk(self):
        checks = [
            {"check": "Document currency verification", "expired_count": 3},
            {"check": "Screening recency check", "is_recent": False},
            {"check": "Policy change applicability check", "trigger_count": 2},
            {"check": "Condition compliance tracking", "conditions_unmet": 2},
            {"check": "Filing deadline monitoring", "overdue_count": 1},
            {"check": "Inter-agent finding consolidation", "open_alerts": 5},
            {"check": "Remediation tracker status", "open_remediation_items": 3},
        ]
        result = _rescore_compliance_risk(checks, {"risk_level": "HIGH"})
        assert result["compliance_risk_score"] >= 70


class TestReviewFrequency:
    def test_low_risk_annual(self):
        result = _recommend_review_frequency({}, {"compliance_risk_score": 15})
        assert result["recommended_frequency"] == "annual"

    def test_high_risk_monthly(self):
        result = _recommend_review_frequency({}, {"compliance_risk_score": 80})
        assert result["recommended_frequency"] == "monthly"


class TestComplianceNarrative:
    def test_clean_narrative(self):
        checks = [
            {"check": "Document currency verification", "expired_count": 0},
            {"check": "Inter-agent finding consolidation", "open_alerts": 0},
            {"check": "Condition compliance tracking", "conditions_unmet": 0},
            {"check": "Compliance risk re-scoring", "compliance_risk_score": 15},
        ]
        result = _generate_compliance_narrative({"company_name": "Test"}, checks)
        assert "good" in result["narrative"].lower() or "complian" in result["narrative"].lower()

    def test_issues_narrative(self):
        checks = [
            {"check": "Document currency verification", "expired_count": 2},
            {"check": "Inter-agent finding consolidation", "open_alerts": 3},
            {"check": "Condition compliance tracking", "conditions_unmet": 1},
            {"check": "Compliance risk re-scoring", "compliance_risk_score": 60},
        ]
        result = _generate_compliance_narrative({"company_name": "Test"}, checks)
        assert "expired" in result["narrative"].lower() or "alert" in result["narrative"].lower()


class TestEscalationClosure:
    def test_close_review(self):
        result = _recommend_escalation_closure({}, {"compliance_risk_score": 15}, [])
        assert result["recommendation"] == "CLOSE_REVIEW"

    def test_escalate(self):
        result = _recommend_escalation_closure({}, {"compliance_risk_score": 80}, [])
        assert result["recommendation"] == "ESCALATE"


class TestAgent10Integration:
    def test_full_execution(self):
        db_path, app_id = _create_test_db()
        result = execute_ongoing_compliance(app_id, {"db_path": db_path})
        assert result["agent_type"] == "ongoing_compliance_review"
        assert len(result["checks_performed"]) == 11
        assert result["compliance_status"] is not None
        assert result["recommended_review_frequency"] is not None
        os.unlink(db_path)

    def test_with_alerts_and_reviews(self):
        db_path, app_id = _create_test_db(add_alerts=True, add_reviews=True)
        result = execute_ongoing_compliance(app_id, {"db_path": db_path})
        assert result["compliance_risk_score"] is not None
        assert len(result["checks_performed"]) == 11
        os.unlink(db_path)

    def test_high_risk_entity(self):
        db_path, app_id = _create_test_db(app_overrides={"risk_level": "VERY_HIGH"})
        result = execute_ongoing_compliance(app_id, {"db_path": db_path})
        assert result["compliance_risk_score"] >= 50
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# MONITORING DATA HELPER Tests
# ═══════════════════════════════════════════════════════════

class TestGetMonitoringData:
    def test_returns_empty_on_missing_tables(self):
        """When monitoring tables don't exist, returns empty lists."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE applications (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO applications (id) VALUES ('test')")
        db.commit()
        db.close()

        result = _get_monitoring_data(db_path, "test")
        assert result["alerts"] == []
        assert result["reviews"] == []
        os.unlink(db_path)

    def test_returns_data(self):
        db_path, app_id = _create_test_db(add_alerts=True, add_reviews=True)
        result = _get_monitoring_data(db_path, app_id)
        assert len(result["alerts"]) > 0
        assert len(result["reviews"]) > 0
        os.unlink(db_path)
