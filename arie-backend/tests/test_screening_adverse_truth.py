import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from screening_adverse_truth import (
    EFFECT_ALLOW,
    EFFECT_BLOCK,
    EFFECT_COMPLIANCE,
    EFFECT_PROHIBITED,
    FRESHNESS_EXPIRED,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    STATE_ADVERSE_MEDIA_FALSE_POSITIVE,
    STATE_PROVIDER_FAILED,
    build_screening_adverse_truth_summary,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "screening_adverse_sot" / "fixtures.json"
FIXTURE_NOW = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _fixtures():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_fixtures().keys()))
def test_deterministic_screening_adverse_sot_fixture_matrix(name):
    fixture = _fixtures()[name]
    payload = fixture["input"]
    expected = fixture["expected"]

    summary = build_screening_adverse_truth_summary(
        payload["app"],
        prescreening=payload["prescreening"],
        screening_reviews=payload["screening_reviews"],
        monitoring_alerts=payload["monitoring_alerts"],
        monitoring_alert_evidence=payload["monitoring_alert_evidence"],
        now=FIXTURE_NOW,
    )

    assert summary["provider"] == "complyadvantage"
    assert summary["provider_authority"]["screening_adverse_media"] == "complyadvantage"
    assert summary["provider_authority"]["idv"] == "sumsub"
    assert summary["provider_authority"]["sumsub_screening_authoritative"] is False
    assert summary["state"] == expected["stored_backend_state"]
    assert summary["approval_effect"] == expected["approval_effect"]
    assert bool(summary["blocking_reasons"]) is bool(expected["case_command_centre_item"])


@pytest.mark.parametrize(
    ("name", "freshness"),
    [
        ("clean_no_hit", FRESHNESS_FRESH),
        ("stale_screening", FRESHNESS_STALE),
        ("expired_screening", FRESHNESS_EXPIRED),
    ],
)
def test_screening_freshness_states_are_explicit(name, freshness):
    fixture = _fixtures()[name]["input"]

    summary = build_screening_adverse_truth_summary(
        fixture["app"],
        prescreening=fixture["prescreening"],
        screening_reviews=fixture["screening_reviews"],
        monitoring_alerts=fixture["monitoring_alerts"],
        monitoring_alert_evidence=fixture["monitoring_alert_evidence"],
        now=FIXTURE_NOW,
    )

    assert summary["freshness"] == freshness


def test_adverse_media_source_url_and_unavailable_source_states_are_represented():
    fixtures = _fixtures()

    with_url = fixtures["adverse_media_with_source_link"]["input"]
    with_url_summary = build_screening_adverse_truth_summary(
        with_url["app"],
        prescreening=with_url["prescreening"],
        screening_reviews=with_url["screening_reviews"],
        monitoring_alerts=with_url["monitoring_alerts"],
        monitoring_alert_evidence=with_url["monitoring_alert_evidence"],
        now=FIXTURE_NOW,
    )

    assert with_url_summary["approval_effect"] == EFFECT_COMPLIANCE
    assert with_url_summary["adverse_media_sources"][0]["source_url_status"] == "available"
    assert with_url_summary["adverse_media_sources"][0]["source_url"] == "https://evidence.example.test/article"

    no_url = fixtures["adverse_media_unavailable_source_link"]["input"]
    no_url_summary = build_screening_adverse_truth_summary(
        no_url["app"],
        prescreening=no_url["prescreening"],
        screening_reviews=no_url["screening_reviews"],
        monitoring_alerts=no_url["monitoring_alerts"],
        monitoring_alert_evidence=no_url["monitoring_alert_evidence"],
        now=FIXTURE_NOW,
    )

    source = no_url_summary["adverse_media_sources"][0]
    assert source["source_url_status"] == "unavailable"
    assert source["source_url_available"] is False
    assert source["source_url_unavailable_reason"] == "Source article link not available from ComplyAdvantage Mesh payload."


def test_adverse_media_false_positive_allows_direct_approval_after_disposition():
    fixture = _fixtures()["adverse_media_false_positive"]["input"]

    summary = build_screening_adverse_truth_summary(
        fixture["app"],
        prescreening=fixture["prescreening"],
        screening_reviews=fixture["screening_reviews"],
        monitoring_alerts=fixture["monitoring_alerts"],
        monitoring_alert_evidence=fixture["monitoring_alert_evidence"],
        now=FIXTURE_NOW,
    )

    assert summary["state"] == STATE_ADVERSE_MEDIA_FALSE_POSITIVE
    assert summary["approval_effect"] == EFFECT_ALLOW
    assert summary["allow_direct_approval"] is True


def test_false_positive_clearance_routes_direct_after_disposition_is_persisted(db):
    from security_hardening import classify_approval_route, collect_approval_gate_blockers

    fixture = _fixtures()["false_positive_cleared"]["input"]
    review = fixture["screening_reviews"][0]
    app = _app_with_prescreening("false-positive-route", fixture["prescreening"])
    db.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition, disposition_code,
             rationale, reviewer_id, reviewer_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app["id"],
            review["subject_type"],
            review["subject_name"],
            review["disposition"],
            review["disposition_code"],
            review["rationale"],
            review["reviewer_id"],
            "Test Compliance Officer",
            review["created_at"],
            review["created_at"],
        ),
    )
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review["reviewer_id"],
            "Test Compliance Officer",
            "co",
            "Screening Review",
            app["ref"],
            json.dumps({
                "subject_name": review["subject_name"],
                "disposition_code": review["disposition_code"],
                "evidence_reference": "test://false-positive-clearance",
            }),
            "127.0.0.1",
        ),
    )
    db.commit()

    route = classify_approval_route(app, db)
    blockers = collect_approval_gate_blockers(app, db)

    assert route["route"] == "direct_low_medium"
    assert "screening_adverse_truth" not in {blocker.get("id") for blocker in blockers}


def test_legacy_sumsub_screening_is_non_authoritative_even_when_idv_is_clean():
    prescreening = {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": "2026-06-01T00:00:00Z",
            "company_screening": {
                "provider": "sumsub",
                "source": "sumsub",
                "api_status": "live",
                "matched": False,
                "results": [],
            },
        },
        "screening_valid_until": "2026-09-01T00:00:00Z",
    }
    app = {
        "id": "sumsub-idv-clean-cannot-screen",
        "risk_level": "LOW",
        "sumsub_idv_status": "verified",
        "sumsub_idv_approval_ready": True,
    }

    summary = build_screening_adverse_truth_summary(
        app,
        prescreening=prescreening,
        now=FIXTURE_NOW,
    )

    assert summary["legacy_sumsub_screening_present"] is True
    assert summary["legacy_non_authoritative"] is True
    assert summary["approval_effect"] == EFFECT_BLOCK
    assert summary["allow_direct_approval"] is False


def test_provider_failure_and_sanctions_have_distinct_approval_effects():
    fixtures = _fixtures()
    provider_failure = fixtures["provider_unavailable"]["input"]
    sanctions = fixtures["sanctions_prohibited_hit"]["input"]

    provider_summary = build_screening_adverse_truth_summary(
        provider_failure["app"],
        prescreening=provider_failure["prescreening"],
        now=FIXTURE_NOW,
    )
    sanctions_summary = build_screening_adverse_truth_summary(
        sanctions["app"],
        prescreening=sanctions["prescreening"],
        now=FIXTURE_NOW,
    )

    assert provider_summary["state"] == STATE_PROVIDER_FAILED
    assert provider_summary["approval_effect"] == EFFECT_BLOCK
    assert sanctions_summary["approval_effect"] == EFFECT_PROHIBITED
    assert sanctions_summary["prohibited_fail_closed"] is True


def _app_with_prescreening(app_id, prescreening, *, risk_level="LOW"):
    return {
        "id": app_id,
        "ref": f"ARF-{app_id.upper()}",
        "status": "kyc_submitted",
        "risk_level": risk_level,
        "final_risk_level": risk_level,
        "risk_score": 25 if risk_level == "LOW" else 45,
        "company_name": f"{app_id} Ltd",
        "prescreening_data": json.dumps(prescreening),
    }


def test_approval_gate_uses_ca_truth_and_blocks_legacy_sumsub_screening(db):
    from security_hardening import collect_approval_gate_blockers

    prescreening = {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": "2026-06-01T00:00:00Z",
            "company_screening": {
                "provider": "sumsub",
                "source": "sumsub",
                "api_status": "live",
                "matched": False,
                "results": [],
            },
        },
        "screening_valid_until": "2026-09-01T00:00:00Z",
    }
    app = _app_with_prescreening("legacy-sumsub-gate", prescreening)

    blockers = collect_approval_gate_blockers(app, db)
    sot_blocker = next(blocker for blocker in blockers if blocker["id"] == "screening_adverse_truth")

    assert "ComplyAdvantage Mesh" in sot_blocker["description"]
    assert sot_blocker["metadata"]["approval_effect"] == EFFECT_BLOCK
    assert "unresolved" in sot_blocker["metadata"]["states"]


def test_clean_ca_screening_does_not_create_screening_adverse_blocker(db):
    from security_hardening import collect_approval_gate_blockers

    fixture = _fixtures()["clean_no_hit"]["input"]
    app = _app_with_prescreening("clean-ca-gate", fixture["prescreening"])

    blockers = collect_approval_gate_blockers(app, db)

    assert "screening_adverse_truth" not in {blocker.get("id") for blocker in blockers}


def test_stale_ca_screening_uses_prescreening_input_timestamp_in_approval_gate(db):
    from security_hardening import collect_approval_gate_blockers

    fixture = _fixtures()["stale_screening"]["input"]
    app = _app_with_prescreening("stale-ca-gate", fixture["prescreening"])

    blockers = collect_approval_gate_blockers(app, db)
    sot_blocker = next(blocker for blocker in blockers if blocker["id"] == "screening_adverse_truth")

    assert sot_blocker["metadata"]["approval_effect"] == EFFECT_BLOCK
    assert sot_blocker["metadata"]["freshness"] == FRESHNESS_STALE
    assert "stale" in sot_blocker["metadata"]["states"]


def test_adverse_media_alert_drives_compliance_route_and_ccc_blocker_state(db):
    from security_hardening import classify_approval_route, collect_approval_gate_blockers

    fixture = _fixtures()["adverse_media_with_source_link"]["input"]
    app = _app_with_prescreening("adverse-ca-gate", fixture["prescreening"], risk_level="MEDIUM")
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, provider, alert_type, severity, detected_by,
             summary, status, officer_action, discovered_via)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            9101,
            app["id"],
            "complyadvantage",
            "adverse_media",
            "high",
            "complyadvantage",
            "Adverse media found",
            "open",
            None,
            "manual",
        ),
    )
    db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, evidence_type,
             match_category, source_title, source_name, source_url,
             source_url_available, evidence_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            9101,
            app["id"],
            "complyadvantage",
            "adverse_media",
            "Adverse Media",
            "Regulatory article",
            "Example News",
            "https://evidence.example.test/article",
            1,
            "hash-adverse-ca-gate",
        ),
    )
    db.commit()

    route = classify_approval_route(app, db)
    blockers = collect_approval_gate_blockers(app, db)
    sot_blocker = next(blocker for blocker in blockers if blocker["id"] == "screening_adverse_truth")

    assert route["route"] == "compliance_required"
    assert "adverse_media" in route["escalation_reasons"]
    assert sot_blocker["metadata"]["approval_effect"] == EFFECT_COMPLIANCE
    assert "adverse_media_hit" in sot_blocker["metadata"]["states"]


def test_case_command_centre_prefers_screening_adverse_truth_summary():
    html = (Path(__file__).resolve().parents[2] / "arie-backoffice.html").read_text(encoding="utf-8")

    assert "screeningAdverseTruthSummary: detail.screening_adverse_truth_summary || null" in html
    assert "screening_adverse_truth_summary: suppliedAdverseTruth || null" in html
    assert "screening.screening_adverse_truth_summary || screening.screening_truth_summary" in html
    assert "effect === 'submit_to_compliance_required'" in html
    assert "effect === 'prohibited_fail_closed'" in html
