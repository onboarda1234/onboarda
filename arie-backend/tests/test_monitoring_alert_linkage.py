import json
import os
import sqlite3
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_alert_linkage as linkage


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE clients (id TEXT PRIMARY KEY);
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            client_id TEXT,
            company_name TEXT
        );
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            client_name TEXT,
            alert_type TEXT,
            source_reference TEXT,
            provider TEXT,
            case_identifier TEXT
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            person_id TEXT,
            person_type TEXT,
            doc_type TEXT,
            slot_key TEXT,
            is_current BOOLEAN,
            version INTEGER,
            superseded_by_document_id TEXT,
            expiry_date TEXT,
            valid_until TEXT,
            verification_status TEXT,
            verification_results TEXT,
            verified_at TEXT,
            review_status TEXT,
            reviewer_role TEXT,
            review_comment TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            superseded_at TEXT
        );
        CREATE TABLE agent_executions (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            document_id TEXT,
            agent_name TEXT,
            agent_number INTEGER,
            status TEXT,
            requires_review BOOLEAN,
            started_at TEXT,
            completed_at TEXT,
            error_message TEXT
        );
        CREATE TABLE directors (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            person_key TEXT,
            full_name TEXT
        );
        CREATE TABLE ubos (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            person_key TEXT,
            full_name TEXT
        );
        CREATE TABLE intermediaries (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            person_key TEXT,
            entity_name TEXT
        );
        CREATE TABLE application_enhanced_requirements (
            id INTEGER PRIMARY KEY,
            monitoring_alert_id INTEGER,
            application_id TEXT,
            active BOOLEAN,
            status TEXT,
            monitoring_document_id TEXT,
            linked_document_id TEXT
        );
        CREATE TABLE monitoring_alert_evidence (
            id INTEGER PRIMARY KEY,
            monitoring_alert_id INTEGER,
            application_id TEXT,
            provider TEXT,
            case_identifier TEXT,
            alert_identifier TEXT,
            match_identifier TEXT,
            risk_identifier TEXT,
            profile_identifier TEXT,
            evidence_status TEXT,
            fetched_at TEXT,
            created_at TEXT
        );
        CREATE TABLE screening_reviews (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            subject_type TEXT,
            subject_name TEXT,
            disposition TEXT,
            disposition_code TEXT,
            rationale TEXT,
            notes TEXT,
            requires_four_eyes BOOLEAN,
            reviewer_id TEXT,
            reviewer_name TEXT,
            second_reviewer_id TEXT,
            second_disposition_code TEXT,
            second_rationale TEXT,
            second_reviewed_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY,
            action TEXT,
            target TEXT,
            timestamp TEXT,
            detail TEXT
        );
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            role TEXT,
            status TEXT
        );
        CREATE TABLE screening_reports_normalized (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            client_id TEXT,
            provider TEXT,
            normalized_report_json TEXT,
            normalization_status TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO clients(id) VALUES ('client-1')")
    conn.execute(
        "INSERT INTO applications(id, ref, client_id, company_name) VALUES (?,?,?,?)",
        ("app-1", "APP-001", "client-1", "Canonical Entity Ltd"),
    )
    conn.executemany(
        "INSERT INTO users(id, role, status) VALUES (?,?,?)",
        [
            ("officer-1", "co", "active"),
            ("officer-2", "sco", "active"),
        ],
    )
    conn.commit()
    return conn


def _document_alert(conn, *, alert_id=1, alert_type="document_expired", source="document:doc-1"):
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, client_name, alert_type, source_reference)
        VALUES (?, 'app-1', 'Canonical Entity Ltd', ?, ?)
        """,
        (alert_id, alert_type, source),
    )


def _document(
    conn,
    *,
    document_id="doc-1",
    application_id="app-1",
    person_id=None,
    person_type=None,
    slot_key="passport:entity",
    is_current=1,
    version=1,
    superseded_by=None,
    expiry_date="2000-01-01",
    verification="flagged",
    review="accepted",
    verification_results=None,
    verified_at=None,
    reviewer_role=None,
    review_comment=None,
    reviewed_by=None,
    reviewed_at=None,
):
    conn.execute(
        """
        INSERT INTO documents
            (id, application_id, person_id, person_type, doc_type, slot_key,
             is_current, version, superseded_by_document_id, expiry_date,
             verification_status, verification_results, verified_at,
             review_status, reviewer_role, review_comment, reviewed_by,
             reviewed_at)
        VALUES (?, ?, ?, ?, 'passport', ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            application_id,
            person_id,
            person_type,
            slot_key,
            is_current,
            version,
            superseded_by,
            expiry_date,
            verification,
            (
                json.dumps(verification_results, sort_keys=True)
                if verification_results is not None
                else None
            ),
            verified_at,
            review,
            reviewer_role,
            review_comment,
            reviewed_by,
            reviewed_at,
        ),
    )


def _agent1_verified_execution(conn, *, document_id="doc-2", execution_id=1):
    conn.execute(
        """
        INSERT INTO agent_executions
            (id, application_id, document_id, agent_name, agent_number, status,
             requires_review, started_at, completed_at)
        VALUES (?, 'app-1', ?, 'verify_document', 1, 'verified', 0,
                '2026-08-01T00:00:00Z', '2026-08-01T00:01:00Z')
        """,
        (execution_id, document_id),
    )


def _screening_alert(
    conn,
    *,
    alert_id=10,
    alert_type="media",
    subject=None,
    case_id="case-1",
    provider_alert_id="provider-alert-1",
    normalized_record_id=None,
):
    subject = subject or {"kind": "entity", "scope": "entity"}
    source = {
        "provider": "complyadvantage",
        "case_identifier": case_id,
        "screening_subject": subject,
    }
    if provider_alert_id is not None:
        source["alert_identifier"] = provider_alert_id
    if normalized_record_id is not None:
        source["normalized_record_id"] = normalized_record_id
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, client_name, alert_type, source_reference,
             provider, case_identifier)
        VALUES (?, 'app-1', 'provider-customer-1', ?, ?, 'complyadvantage', ?)
        """,
        (alert_id, alert_type, json.dumps(source, sort_keys=True), case_id),
    )


def _evidence(
    conn,
    *,
    alert_id=10,
    application_id="app-1",
    case_id="case-1",
    provider_alert_id="provider-alert-1",
):
    conn.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier,
             alert_identifier, match_identifier, evidence_status, fetched_at,
             created_at)
        VALUES (?, ?, 'complyadvantage', ?, ?, 'match-1',
                'fetched', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')
        """,
        (alert_id, application_id, case_id, provider_alert_id),
    )


def _normalized_snapshot(
    conn,
    *,
    record_id=1,
    subject=None,
    status="success",
    updated_at="2026-08-01T00:00:00Z",
):
    subject = subject or {"kind": "entity", "scope": "entity", "person_key": ""}
    report = {
        "screening_subject_kind": subject.get("kind"),
        "subject_scope": subject.get("scope"),
        "screening_subject_person_key": subject.get("person_key"),
        "provider_specific": {"complyadvantage": {"screening_subject": subject}},
    }
    report["provider_specific"]["complyadvantage"]["provider_references"] = {
        "case_ids": ["case-1"],
        "alert_ids": ["provider-alert-1"],
    }
    conn.execute(
        """
        INSERT INTO screening_reports_normalized
            (id, application_id, client_id, provider, normalized_report_json,
             normalization_status, created_at, updated_at)
        VALUES (?, 'app-1', 'client-1', 'complyadvantage', ?, ?, ?, ?)
        """,
        (record_id, json.dumps(report, sort_keys=True), status, updated_at, updated_at),
    )


def _screening_review_audit(
    conn,
    *,
    subject_name="Canonical Entity Ltd",
    subject_type="entity",
    disposition_code="false_positive_cleared",
    actor="officer-1",
    actor_role="co",
    rationale="Exact reviewed evidence",
    requires_four_eyes=False,
    four_eyes_status="complete",
    timestamp="2026-08-01T00:00:00Z",
):
    detail = {
        "application_id": "app-1",
        "subject_name": subject_name,
        "subject_type": subject_type,
        "disposition_code": disposition_code,
        "case_identifier": "case-1",
        "provider": "complyadvantage",
        "actor": actor,
        "actor_role": actor_role,
        "rationale": rationale,
        "requires_four_eyes": requires_four_eyes,
        "four_eyes_status": four_eyes_status,
        "provider_references": {
            "case_ids": ["case-1"],
            "alert_ids": ["provider-alert-1"],
        },
    }
    conn.execute(
        "INSERT INTO audit_log(action, target, timestamp, detail) VALUES ('Screening Review','APP-001',?,?)",
        (timestamp, json.dumps(detail, sort_keys=True)),
    )


def _confirmed_clear_review(conn):
    conn.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition,
             disposition_code, rationale, requires_four_eyes, reviewer_id,
             created_at, updated_at)
        VALUES ('app-1','entity','Canonical Entity Ltd','cleared',
                'false_positive_cleared','Exact reviewed evidence',0,'officer-1',
                '2026-08-01T00:00:00Z','2026-08-01T00:00:00Z')
        """
    )
    _screening_review_audit(conn)


def _four_eyes_clear_review(
    conn,
    *,
    second_reviewer_id=None,
    second_disposition_code=None,
    second_rationale=None,
    second_reviewed_at=None,
    second_actor_role="sco",
):
    conn.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition,
             disposition_code, rationale, requires_four_eyes, reviewer_id,
             second_reviewer_id, second_disposition_code, second_rationale,
             second_reviewed_at, created_at, updated_at)
        VALUES ('app-1','entity','Canonical Entity Ltd','cleared',
                'false_positive_cleared','Exact reviewed evidence',1,'officer-1',
                ?,?,?,?,'2026-08-01T00:00:00Z','2026-08-01T00:00:00Z')
        """,
        (
            second_reviewer_id,
            second_disposition_code,
            second_rationale,
            second_reviewed_at,
        ),
    )
    if second_reviewer_id:
        _screening_review_audit(
            conn,
            actor=second_reviewer_id,
            actor_role=second_actor_role,
            rationale=second_rationale or "",
            requires_four_eyes=True,
            four_eyes_status="second_review_complete",
            timestamp=second_reviewed_at or "2026-08-01T00:01:00Z",
        )
    else:
        _screening_review_audit(
            conn,
            requires_four_eyes=True,
            four_eyes_status="second_review_required",
        )


def test_document_linkage_uses_exact_id_and_is_read_only():
    conn = _db()
    _document_alert(conn)
    _document(conn)
    conn.commit()
    before = conn.total_changes

    result = linkage.resolve_alert_linkage(conn, 1)

    assert result["linkage_type"] == "document_expiry"
    assert result["document"]["trigger_document_id"] == "doc-1"
    assert result["document"]["canonical_document_id"] == "doc-1"
    assert result["document"]["trigger_expiry_date"] == "2000-01-01"
    assert result["application"]["customer_id"] == "client-1"
    assert result["owner"]["module"] == "kyc_documents"
    assert result["read_only"] is True
    assert result["mutation_controls"] is False
    assert conn.total_changes == before


@pytest.mark.parametrize(
    "source",
    ["doc-1", "document:doc-1:extra", '{"document_id":"doc-1"}', "Document:doc-1"],
)
def test_document_linkage_has_no_alias_or_json_fallback(source):
    conn = _db()
    _document_alert(conn, source=source)
    _document(conn)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.code == "linkage_missing"
    assert exc.value.reasons == ["missing_exact_document_reference"]


def test_document_wrong_application_is_rejected_without_leaking_other_app():
    conn = _db()
    conn.execute("INSERT INTO clients(id) VALUES ('client-2')")
    conn.execute(
        "INSERT INTO applications(id, ref, client_id, company_name) VALUES ('app-2','APP-002','client-2','Other')"
    )
    _document_alert(conn)
    _document(conn, application_id="app-2")

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.code == "linkage_application_mismatch"
    assert "app-2" not in exc.value.public_message


def test_document_person_requires_exact_canonical_party_row_id():
    conn = _db()
    conn.execute(
        "INSERT INTO directors(id, application_id, person_key, full_name) VALUES ('director-1','app-1','legacy-key','Same Name')"
    )
    _document_alert(conn)
    _document(conn, person_id="director-1", person_type="director", slot_key="passport:director-1")

    result = linkage.resolve_alert_linkage(conn, 1)
    assert result["document"]["person_id"] == "director-1"
    assert result["document"]["person_type"] == "director"

    conn.execute("UPDATE documents SET person_id='legacy-key' WHERE id='doc-1'")
    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)
    assert exc.value.reasons == ["missing_document_owner"]


def test_document_ambiguous_current_version_fails_closed():
    conn = _db()
    _document_alert(conn)
    _document(conn, document_id="doc-1", version=1)
    _document(conn, document_id="doc-2", version=2)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.code == "linkage_ambiguous"
    assert exc.value.reasons == ["ambiguous_current_document"]


@pytest.mark.parametrize("version", [None, 0, -1])
def test_document_missing_or_invalid_version_fails_closed(version):
    conn = _db()
    _document_alert(conn)
    _document(conn, version=version)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["invalid_trigger_document_version"]


@pytest.mark.parametrize(
    ("expiry_date", "expected_state"),
    [
        ("2999-01-01", "stale"),
        (None, "unavailable"),
        ("not-a-date", "unavailable"),
    ],
)
def test_stale_document_expired_signal_never_projects_expired(
    expiry_date,
    expected_state,
):
    conn = _db()
    _document_alert(conn)
    _document(conn, expiry_date=expiry_date)

    result = linkage.resolve_alert_linkage(conn, 1)

    assert result["owner"]["state"]["key"] == expected_state
    assert result["owner"]["state"]["key"] != "expired"


def test_document_current_version_must_be_terminal():
    conn = _db()
    _document_alert(conn)
    _document(conn, superseded_by="unexpected-successor")

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["document_version_chain_broken"]


def test_document_preserves_trigger_and_current_replacement_versions():
    conn = _db()
    _document_alert(conn)
    _document(
        conn,
        document_id="doc-1",
        is_current=0,
        version=1,
        superseded_by="doc-2",
    )
    _document(
        conn,
        document_id="doc-2",
        is_current=1,
        version=2,
        verification="verified",
        review="accepted",
        verification_results={
            "overall": "verified",
            "checks": [{"name": "authenticity", "result": "pass"}],
        },
        verified_at="2026-08-01T00:00:00Z",
    )
    _agent1_verified_execution(conn)

    result = linkage.resolve_alert_linkage(conn, 1)

    assert result["document"]["trigger_document_id"] == "doc-1"
    assert result["document"]["trigger_document_version"] == 1
    assert result["document"]["canonical_document_id"] == "doc-2"
    assert result["document"]["current_document_version"] == 2
    assert result["owner"]["state"]["key"] == "verified"
    assert result["owner"]["state"]["reliance_state"] == "verified"
    assert result["owner"]["state"]["verification_method"] == "agent1"
    assert result["navigation"]["document_id"] == "doc-2"


def test_raw_verified_document_without_canonical_evidence_is_not_verified():
    conn = _db()
    _document_alert(conn)
    _document(conn, document_id="doc-1", is_current=0, superseded_by="doc-2")
    _document(
        conn,
        document_id="doc-2",
        is_current=1,
        version=2,
        verification="verified",
        review="accepted",
    )

    result = linkage.resolve_alert_linkage(conn, 1)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["reliance_state"] == "missing_verification_results"
    assert result["owner"]["state"]["verification_method"] is None


def test_pending_replacement_is_received_not_verifying():
    conn = _db()
    _document_alert(conn)
    _document(conn, document_id="doc-1", is_current=0, superseded_by="doc-2")
    _document(
        conn,
        document_id="doc-2",
        is_current=1,
        version=2,
        verification="pending",
    )

    result = linkage.resolve_alert_linkage(conn, 1)

    assert result["owner"]["state"]["key"] == "replacement_received"


def test_document_request_identity_mismatch_fails_closed():
    conn = _db()
    _document_alert(conn)
    _document(conn)
    conn.execute(
        """
        INSERT INTO application_enhanced_requirements
            (monitoring_alert_id, application_id, active, status,
             monitoring_document_id)
        VALUES (1, 'other-app', 1, 'requested', 'doc-1')
        """
    )

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["document_request_identity_mismatch"]


@pytest.mark.parametrize(
    ("monitoring_document_id", "linked_document_id"),
    [(None, "doc-1"), ("doc-1", None), (None, None)],
)
def test_document_request_requires_both_exact_document_ids(
    monitoring_document_id,
    linked_document_id,
):
    conn = _db()
    _document_alert(conn)
    _document(conn)
    conn.execute(
        """
        INSERT INTO application_enhanced_requirements
            (monitoring_alert_id, application_id, active, status,
             monitoring_document_id, linked_document_id)
        VALUES (1, 'app-1', 1, 'requested', ?, ?)
        """,
        (monitoring_document_id, linked_document_id),
    )

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["document_request_identity_mismatch"]


def test_multiple_active_document_requests_are_ambiguous():
    conn = _db()
    _document_alert(conn)
    _document(conn)
    conn.executemany(
        """
        INSERT INTO application_enhanced_requirements
            (id, monitoring_alert_id, application_id, active, status,
             monitoring_document_id)
        VALUES (?, 1, 'app-1', 1, 'requested', 'doc-1')
        """,
        [(1,), (2,)],
    )

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["ambiguous_document_request"]


@pytest.mark.parametrize(
    ("request_status", "expected_state"),
    [
        ("generated", "request_not_started"),
        ("waived", "unavailable"),
        ("cancelled", "unavailable"),
        ("unexpected_status", "unavailable"),
    ],
)
def test_document_request_projection_fails_closed_for_nonprojected_states(
    request_status,
    expected_state,
):
    conn = _db()
    _document_alert(conn)
    _document(conn)
    conn.execute(
        """
        INSERT INTO application_enhanced_requirements
            (monitoring_alert_id, application_id, active, status,
             monitoring_document_id, linked_document_id)
        VALUES (1, 'app-1', 1, ?, 'doc-1', 'doc-1')
        """,
        (request_status,),
    )

    result = linkage.resolve_alert_linkage(conn, 1)

    assert result["owner"]["state"]["key"] == expected_state


def test_document_broken_replacement_chain_is_not_guessed():
    conn = _db()
    _document_alert(conn)
    _document(conn, document_id="doc-1", is_current=0, superseded_by="missing")
    _document(conn, document_id="doc-2", is_current=1, version=2)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["document_version_chain_broken"]


def test_document_intermediate_version_owner_drift_is_rejected():
    conn = _db()
    _document_alert(conn)
    _document(conn, document_id="doc-1", is_current=0, superseded_by="doc-2")
    _document(
        conn,
        document_id="doc-2",
        is_current=0,
        version=2,
        superseded_by="doc-3",
        slot_key="different-slot",
    )
    _document(conn, document_id="doc-3", is_current=1, version=3)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 1)

    assert exc.value.reasons == ["document_version_chain_broken"]


def test_screening_entity_linkage_uses_exact_case_and_application_subject():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    _confirmed_clear_review(conn)

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["linkage_type"] == "screening_hit"
    assert result["subject"] == {
        "id": "app-1",
        "person_key": None,
        "type": "entity",
        "scope": "entity",
    }
    assert result["screening_case"]["case_identifier"] == "case-1"
    assert result["screening_case"]["match_identifiers"] == ["match-1"]
    assert result["owner"]["state"]["key"] == "resolved_in_screening_review"


def test_screening_person_linkage_requires_exact_person_key_not_name():
    conn = _db()
    conn.execute(
        "INSERT INTO directors(id, application_id, person_key, full_name) VALUES ('dir-1','app-1','person-key-1','Canonical Person')"
    )
    _screening_alert(
        conn,
        subject={"kind": "director", "scope": "person", "person_key": "person-key-1"},
    )
    _evidence(conn)
    conn.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition,
             disposition_code, rationale, requires_four_eyes, reviewer_id,
             created_at, updated_at)
        VALUES ('app-1','director','Canonical Person','follow_up_required',
                'needs_more_information','Exact follow-up evidence',0,'officer-1',
                '2026-08-01T00:00:00Z',
                '2026-08-01T00:00:00Z')
        """
    )
    _screening_review_audit(
        conn,
        subject_name="Canonical Person",
        subject_type="director",
        disposition_code="needs_more_information",
        rationale="Exact follow-up evidence",
    )

    result = linkage.resolve_alert_linkage(conn, 10)
    assert result["subject"]["id"] == "dir-1"
    assert result["subject"]["person_key"] == "person-key-1"
    assert result["owner"]["state"]["key"] == "review_required"

    conn.execute("UPDATE directors SET person_key='changed-key' WHERE id='dir-1'")
    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)
    assert exc.value.reasons == ["missing_screening_subject"]


def test_screening_person_without_stable_key_requires_manual_review():
    conn = _db()
    _screening_alert(conn, subject={"kind": "subject", "scope": "person"})
    _evidence(conn)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["missing_stable_screening_subject"]


def test_screening_case_or_provider_disagreement_fails_closed():
    conn = _db()
    _screening_alert(conn)
    conn.execute("UPDATE monitoring_alerts SET case_identifier='different-case' WHERE id=10")

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["screening_case_identity_mismatch"]


def test_screening_evidence_cross_application_is_rejected():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn, application_id="other-app")

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["screening_evidence_identity_mismatch"]


def test_screening_evidence_without_application_id_is_rejected():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn, application_id=None)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["screening_evidence_identity_mismatch"]


def test_screening_multiple_hits_are_not_treated_as_ambiguous():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    conn.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier,
             alert_identifier, match_identifier, evidence_status, fetched_at,
             created_at)
        VALUES (10, 'app-1', 'complyadvantage', 'case-1',
                'provider-alert-1', 'match-2', 'fetched',
                '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')
        """
    )
    result = linkage.resolve_alert_linkage(conn, 10)
    assert result["screening_case"]["evidence_count"] == 2
    assert result["screening_case"]["match_identifiers"] == ["match-1", "match-2"]


def test_screening_duplicate_identity_includes_exact_provider_alert_reference():
    conn = _db()
    _screening_alert(conn, alert_id=10, provider_alert_id="provider-alert-1")
    _screening_alert(conn, alert_id=11, provider_alert_id="provider-alert-2")
    _evidence(conn, alert_id=10, provider_alert_id="provider-alert-1")
    _evidence(conn, alert_id=11, provider_alert_id="provider-alert-2")

    distinct = linkage.build_linkage_plan(conn)

    assert distinct["counts"]["duplicate_linkage_groups"] == 0
    assert all(not row["duplicate_linkage"] for row in distinct["rows"])

    conn.execute(
        """
        UPDATE monitoring_alerts
           SET source_reference = ?
         WHERE id = 11
        """,
        (
            json.dumps(
                {
                    "provider": "complyadvantage",
                    "case_identifier": "case-1",
                    "alert_identifier": "provider-alert-1",
                    "screening_subject": {"kind": "entity", "scope": "entity"},
                },
                sort_keys=True,
            ),
        ),
    )
    conn.execute(
        """
        UPDATE monitoring_alert_evidence
           SET alert_identifier = 'provider-alert-1'
         WHERE monitoring_alert_id = 11
        """
    )

    duplicate = linkage.build_linkage_plan(conn)

    assert duplicate["counts"]["duplicate_linkage_groups"] == 1
    assert duplicate["duplicate_groups"][0]["alert_ids"] == [10, 11]


def test_screening_missing_provider_reference_is_not_guessed_duplicate():
    conn = _db()
    _screening_alert(conn, alert_id=10, provider_alert_id=None)
    _screening_alert(conn, alert_id=11, provider_alert_id=None)
    _evidence(conn, alert_id=10, provider_alert_id=None)
    _evidence(conn, alert_id=11, provider_alert_id=None)

    plan = linkage.build_linkage_plan(conn)

    assert plan["counts"]["duplicate_linkage_groups"] == 0
    assert all(not row["duplicate_linkage"] for row in plan["rows"])


def test_screening_evidence_without_timestamp_never_projects_resolved():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    conn.execute(
        """
        UPDATE monitoring_alert_evidence
           SET fetched_at = NULL, created_at = NULL
         WHERE monitoring_alert_id = 10
        """
    )
    _confirmed_clear_review(conn)

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["screening_case"]["evidence_available"] is False
    assert result["screening_case"]["evidence_timestamps_complete"] is False


def test_screening_four_eyes_pending_never_projects_clear():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    _four_eyes_clear_review(conn)

    result = linkage.resolve_alert_linkage(conn, 10)
    assert result["owner"]["state"]["key"] == "pending_second_review"
    assert result["owner"]["state"]["four_eyes_status"] == "pending_second_review"


def test_screening_four_eyes_same_reviewer_never_projects_resolved():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    _four_eyes_clear_review(
        conn,
        second_reviewer_id="officer-1",
        second_disposition_code="false_positive_cleared",
        second_rationale="Purported second review",
        second_reviewed_at="2026-08-01T00:01:00Z",
        second_actor_role="co",
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["audit_confirmed"] is False


@pytest.mark.parametrize(
    ("second_reviewer_id", "role", "status"),
    [
        ("officer-3", "co", "active"),
        ("officer-4", "sco", "inactive"),
    ],
)
def test_screening_four_eyes_non_senior_or_inactive_second_reviewer_fails_closed(
    second_reviewer_id,
    role,
    status,
):
    conn = _db()
    conn.execute(
        "INSERT INTO users(id, role, status) VALUES (?,?,?)",
        (second_reviewer_id, role, status),
    )
    _screening_alert(conn)
    _evidence(conn)
    _four_eyes_clear_review(
        conn,
        second_reviewer_id=second_reviewer_id,
        second_disposition_code="false_positive_cleared",
        second_rationale="Purported second review",
        second_reviewed_at="2026-08-01T00:01:00Z",
        second_actor_role=role,
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["audit_confirmed"] is False


@pytest.mark.parametrize(
    ("second_rationale", "second_reviewed_at"),
    [
        (None, "2026-08-01T00:01:00Z"),
        ("Independent second review", None),
    ],
)
def test_screening_four_eyes_missing_second_rationale_or_timestamp_fails_closed(
    second_rationale,
    second_reviewed_at,
):
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    _four_eyes_clear_review(
        conn,
        second_reviewer_id="officer-2",
        second_disposition_code="false_positive_cleared",
        second_rationale=second_rationale,
        second_reviewed_at=second_reviewed_at,
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["audit_confirmed"] is False


def test_screening_four_eyes_distinct_active_sco_with_exact_audit_resolves():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    _four_eyes_clear_review(
        conn,
        second_reviewer_id="officer-2",
        second_disposition_code="false_positive_cleared",
        second_rationale="Independent second review",
        second_reviewed_at="2026-08-01T00:01:00Z",
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "resolved_in_screening_review"
    assert result["owner"]["state"]["four_eyes_status"] == "complete"
    assert result["owner"]["state"]["audit_confirmed"] is True


def test_screening_unconfirmed_clear_never_projects_resolved():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    conn.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition,
             disposition_code, rationale, requires_four_eyes, reviewer_id,
             created_at, updated_at)
        VALUES ('app-1','entity','Canonical Entity Ltd','cleared',
                'false_positive_cleared','Exact reviewed evidence',0,'officer-1',
                '2026-08-01T00:00:00Z','2026-08-01T00:00:00Z')
        """
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["audit_confirmed"] is False


def test_screening_unconfirmed_nonterminal_review_never_projects_owner_state():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    conn.execute(
        """
        INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition,
             disposition_code, rationale, requires_four_eyes, reviewer_id,
             created_at, updated_at)
        VALUES ('app-1','entity','Canonical Entity Ltd','follow_up_required',
                'needs_more_information','Context only',0,'officer-1',
                '2026-08-01T00:00:00Z','2026-08-01T00:00:00Z')
        """
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["audit_confirmed"] is False


def test_screening_audit_reference_lists_must_be_typed_arrays():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    _confirmed_clear_review(conn)
    malformed_detail = {
        "subject_name": "Canonical Entity Ltd",
        "disposition_code": "false_positive_cleared",
        "provider": "complyadvantage",
        "provider_references": {
            "case_ids": "case-1",
            "alert_ids": "provider-alert-1",
        },
    }
    conn.execute(
        "UPDATE audit_log SET detail=? WHERE action='Screening Review'",
        (json.dumps(malformed_detail, sort_keys=True),),
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"
    assert result["owner"]["state"]["audit_confirmed"] is False


def test_screening_without_review_projects_review_required_not_clear():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "review_required"
    assert "clear" not in result["owner"]["state"]["key"]


@pytest.mark.parametrize("status", [None, "", "unavailable", "failed"])
def test_screening_unavailable_evidence_projects_unavailable(status):
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    conn.execute(
        "UPDATE monitoring_alert_evidence SET evidence_status=? WHERE monitoring_alert_id=10",
        (status,),
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["owner"]["state"]["key"] == "unavailable"


def test_screening_without_case_record_is_manual_review_not_false_clear():
    conn = _db()
    _screening_alert(conn)

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["missing_screening_case_record"]


def test_screening_snapshot_must_match_exact_stable_subject():
    conn = _db()
    subject = {"kind": "director", "scope": "person", "person_key": "person-key-1"}
    conn.execute(
        "INSERT INTO directors(id, application_id, person_key, full_name) VALUES ('dir-1','app-1','person-key-1','Canonical Person')"
    )
    _screening_alert(conn, subject=subject, normalized_record_id=1)
    _evidence(conn)
    _normalized_snapshot(
        conn,
        subject={"kind": "director", "scope": "person", "person_key": "other-key"},
    )

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["screening_snapshot_subject_mismatch"]


def test_screening_snapshot_distinguishes_historical_from_current():
    conn = _db()
    _screening_alert(conn, normalized_record_id=1)
    _evidence(conn)
    _normalized_snapshot(conn, record_id=1, updated_at="2026-07-01T00:00:00Z")
    _normalized_snapshot(conn, record_id=2, updated_at="2026-08-01T00:00:00Z")

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["screening_case"]["linked_snapshot_id"] == 1
    assert result["screening_case"]["current_snapshot_id"] == 2
    assert result["screening_case"]["snapshot_role"] == "historical"
    assert result["provenance"]["normalized_snapshot_is_authoritative"] is False


def test_screening_failed_snapshot_never_projects_owner_state():
    conn = _db()
    _screening_alert(conn, normalized_record_id=1)
    _evidence(conn)
    _normalized_snapshot(conn, status="failed")

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["screening_snapshot_not_successful"]


def test_screening_latest_unattributed_failure_projects_unavailable():
    conn = _db()
    _screening_alert(conn, normalized_record_id=1)
    _normalized_snapshot(conn, record_id=1, updated_at="2026-07-01T00:00:00Z")
    conn.execute(
        """
        INSERT INTO screening_reports_normalized
            (id, application_id, client_id, provider, normalized_report_json,
             normalization_status, created_at, updated_at)
        VALUES (2, 'app-1', 'client-1', 'complyadvantage', NULL, 'failed',
                '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
        """
    )

    result = linkage.resolve_alert_linkage(conn, 10)

    assert result["screening_case"]["current_snapshot_status"] == "failed"
    assert result["screening_case"]["current_snapshot_scope"] == "application_provider_unattributed"
    assert result["owner"]["state"]["key"] == "unavailable"


def test_screening_provider_reference_must_match_case_evidence():
    conn = _db()
    _screening_alert(conn)
    _evidence(conn)
    conn.execute(
        "UPDATE monitoring_alert_evidence SET alert_identifier='different-alert' WHERE monitoring_alert_id=10"
    )

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 10)

    assert exc.value.reasons == ["screening_provider_reference_mismatch"]


def test_unsupported_type_is_exact_case_sensitive_422():
    conn = _db()
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, alert_type, source_reference)
        VALUES (20, 'app-1', 'Sanctions Match', 'opaque')
        """
    )

    with pytest.raises(linkage.LinkageError) as exc:
        linkage.resolve_alert_linkage(conn, 20)

    assert exc.value.code == "unsupported_alert_type"
    assert exc.value.http_status == 422


def test_dry_run_is_repeatable_and_makes_no_writes():
    conn = _db()
    _document_alert(conn)
    _document(conn)
    _screening_alert(conn)
    _evidence(conn)
    conn.commit()
    before = conn.total_changes

    first = linkage.build_linkage_plan(conn)
    second = linkage.build_linkage_plan(conn)

    assert first["fingerprint"] == second["fingerprint"]
    assert linkage.plan_without_timestamp(first) == linkage.plan_without_timestamp(second)
    assert first["counts"]["linked_correctly"] == 2
    assert first["counts"]["data_changes_planned"] == 0
    assert first["apply_supported"] is False
    document_row = next(row for row in first["rows"] if row["alert_id"] == 1)
    screening_row = next(row for row in first["rows"] if row["alert_id"] == 10)
    assert document_row["proposed_linkage"]["owner_module"] == "kyc_documents"
    assert document_row["proposed_linkage"]["current_document_version"] == 1
    assert document_row["proposed_linkage"]["navigation"]["document_id"] == "doc-1"
    assert screening_row["proposed_linkage"]["owner_module"] == "screening_review"
    assert screening_row["proposed_linkage"]["case_identifier"] == "case-1"
    assert screening_row["proposed_linkage"]["navigation"]["subject_id"] == "app-1"
    assert conn.total_changes == before


def test_dry_run_fingerprint_changes_when_stable_link_changes():
    conn = _db()
    _document_alert(conn)
    _document(conn)
    first = linkage.build_linkage_plan(conn)
    conn.execute("UPDATE monitoring_alerts SET source_reference='document:missing' WHERE id=1")
    second = linkage.build_linkage_plan(conn)
    assert first["fingerprint"] != second["fingerprint"]


def test_dry_run_preserves_unsupported_orphan_as_independent_findings():
    conn = _db()
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, alert_type, source_reference)
        VALUES (30, 'missing-app', 'Risk Drift', 'opaque')
        """
    )

    plan = linkage.build_linkage_plan(conn)
    row = next(item for item in plan["rows"] if item["alert_id"] == 30)

    assert row["scope_status"] == "unsupported"
    assert row["linkage_classification"] == "orphaned_alert"
    assert row["review_disposition"] == "out_of_scope"
    assert row["data_change_required"] is False
    assert plan["counts"]["orphaned_alert"] == 1
