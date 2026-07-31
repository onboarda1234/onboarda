"""Focused contract tests for monitoring_alert_state_machine_v1."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from urllib.parse import urlsplit

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_alert_state_machine as sm
from db import DBConnection


OFFICER = {
    "sub": "officer-1",
    "name": "Test Officer",
    "role": "co",
}
SENIOR = {
    "sub": "senior-1",
    "name": "Senior Officer",
    "role": "sco",
}


@pytest.fixture
def state_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db = DBConnection(conn, is_postgres=False, database_identity=":memory:")
    db.executescript(
        """
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            provider TEXT,
            case_identifier TEXT,
            alert_type TEXT,
            severity TEXT,
            detected_by TEXT,
            summary TEXT,
            source_reference TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            officer_action TEXT,
            officer_notes TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            linked_periodic_review_id INTEGER,
            linked_edd_case_id INTEGER,
            triaged_at TEXT,
            assigned_at TEXT,
            resolved_at TEXT
        );
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE application_enhanced_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            monitoring_alert_id INTEGER,
            linked_document_id TEXT,
            status TEXT,
            waiver_reason TEXT
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            application_id TEXT
        );
        CREATE TABLE agent_executions (
            id INTEGER PRIMARY KEY,
            application_id TEXT
        );
        CREATE TABLE screening_reviews (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            disposition TEXT
        );
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            stage TEXT,
            linked_monitoring_alert_id INTEGER
        );
        CREATE TABLE periodic_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            status TEXT,
            closed_at TEXT,
            linked_monitoring_alert_id INTEGER
        );
        CREATE TABLE change_alerts (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            source_reference TEXT
        );
        CREATE TABLE change_requests (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            source_alert_id TEXT
        );
        CREATE TABLE monitoring_alert_review_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            state TEXT,
            initiated_by TEXT,
            approved_by TEXT,
            source_alert_status TEXT,
            requested_outcome TEXT,
            dismissal_reason TEXT,
            rationale TEXT,
            evidence_ref TEXT,
            transition_evidence TEXT,
            tier INTEGER,
            second_review_bypassed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE monitoring_alert_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitoring_alert_id INTEGER NOT NULL,
            application_id TEXT,
            provider TEXT NOT NULL,
            case_identifier TEXT
        );
        CREATE TABLE monitoring_alert_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_name TEXT,
            user_role TEXT,
            action TEXT,
            target TEXT,
            detail TEXT,
            ip_address TEXT,
            timestamp TEXT,
            before_state TEXT,
            after_state TEXT,
            previous_hash TEXT,
            entry_hash TEXT,
            application_id TEXT,
            request_id TEXT
        );
        CREATE UNIQUE INDEX audit_log_previous_hash_unique
            ON audit_log(previous_hash) WHERE previous_hash IS NOT NULL;
        """
    )
    for user_id, role in (
        ("officer-1", "co"),
        ("senior-1", "sco"),
        ("assigned-1", "co"),
    ):
        db.execute(
            "INSERT INTO users (id, role, status) VALUES (?, ?, 'active')",
            (user_id, role),
        )
    db.commit()
    yield db
    db.close()


def _insert_alert(
    db,
    *,
    application_id="app-1",
    status="open",
    alert_type="manual",
    severity="medium",
    provider=None,
    case_identifier=None,
    detected_by="officer",
    summary="Controlled test alert",
    source_reference="test-source:1",
    linked_periodic_review_id=None,
    linked_edd_case_id=None,
):
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id, provider, case_identifier, alert_type, severity,
             detected_by, summary, source_reference, status,
             linked_periodic_review_id, linked_edd_case_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application_id,
            provider,
            case_identifier,
            alert_type,
            severity,
            detected_by,
            summary,
            source_reference,
            status,
            linked_periodic_review_id,
            linked_edd_case_id,
        ),
    )
    alert_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    db.commit()
    return alert_id


def _status(db, alert_id):
    return db.execute(
        "SELECT status FROM monitoring_alerts WHERE id = ?", (alert_id,)
    ).fetchone()["status"]


def _audit_rows(db, alert_id):
    return db.execute(
        "SELECT * FROM audit_log WHERE target = ? ORDER BY id",
        (f"monitoring_alert:{alert_id}",),
    ).fetchall()


def _insert_review_control(
    db,
    alert_id,
    state="approved",
    *,
    initiated_by="officer-1",
    approved_by="senior-1",
    source_status="in_review",
    requested_outcome="no_material_impact",
    dismissal_reason=None,
    rationale="Senior evidence assessment.",
    evidence_ref="Documented review evidence.",
    transition_evidence=None,
    tier=1,
    second_review_bypassed=0,
):
    db.execute(
        "INSERT INTO monitoring_alert_review_requests "
        "(alert_id, state, initiated_by, approved_by, source_alert_status, "
        "requested_outcome, dismissal_reason, rationale, evidence_ref, "
        "transition_evidence, tier, second_review_bypassed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            alert_id,
            state,
            initiated_by,
            approved_by,
            source_status,
            requested_outcome,
            dismissal_reason,
            rationale,
            evidence_ref,
            json.dumps(transition_evidence or {}, sort_keys=True),
            tier,
            second_review_bypassed,
        ),
    )
    return db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _insert_document_requirement(
    db,
    alert_id,
    *,
    status="accepted",
    document_id="doc-controlled",
    waiver_reason=None,
):
    db.execute(
        "INSERT INTO documents (id, application_id) VALUES (?, 'app-1')",
        (document_id,),
    )
    db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, monitoring_alert_id, linked_document_id,
             status, waiver_reason)
        VALUES ('app-1', ?, ?, ?, ?)
        """,
        (alert_id, document_id, status, waiver_reason),
    )
    request_id = db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    db.commit()
    return request_id, document_id


def _rule_case(db, rule):
    owner = (
        "manual"
        if "manual" in rule.allowed_owners or "any" in rule.allowed_owners
        else next(item for item in sorted(rule.allowed_owners) if item != "any")
    )
    alert_kwargs = {"status": rule.source, "alert_type": "manual"}
    evidence = {}
    if owner == "documents":
        alert_kwargs.update(
            alert_type="document_expiring",
            detected_by="document_health",
            summary="A routine corporate certificate requires refresh.",
        )
    elif owner == "screening_review":
        alert_kwargs.update(
            alert_type="adverse_media",
            provider="complyadvantage",
            case_identifier="case-1",
        )
    alert_id = _insert_alert(db, **alert_kwargs)

    for alternatives in rule.any_evidence:
        key = alternatives[0]
        evidence[key] = (
            "test-source:1" if key == "source_reference" else "case-1"
        )
    for key in rule.required_evidence:
        if key == "officer_id":
            evidence[key] = "officer-1"
        elif key == "officer_rationale":
            evidence[key] = "Evidence reviewed by the authorised officer."
        elif key == "dismissal_reason":
            evidence[key] = "false_positive"
        elif key == "waiver_reason":
            evidence[key] = "Senior-approved regulatory exception."
        elif key == "periodic_review_id":
            db.execute(
                "INSERT INTO periodic_reviews "
                "(application_id, status) VALUES ('app-1', 'pending')"
            )
            review_id = db.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
            db.execute(
                "UPDATE monitoring_alerts SET linked_periodic_review_id = ? "
                "WHERE id = ?",
                (review_id, alert_id),
            )
            evidence[key] = review_id
        elif key == "edd_case_id":
            db.execute(
                "INSERT INTO edd_cases "
                "(application_id, stage) VALUES ('app-1', 'data_collection')"
            )
            edd_id = db.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
            db.execute(
                "UPDATE monitoring_alerts SET linked_edd_case_id = ? WHERE id = ?",
                (edd_id, alert_id),
            )
            evidence[key] = edd_id
        elif key in {"document_request_id", "document_id"}:
            # Added together after the loop so the linkage is exact.
            continue
        elif key == "escalation_id":
            db.execute(
                "INSERT INTO monitoring_alert_escalations (alert_id) VALUES (?)",
                (alert_id,),
            )
            evidence[key] = db.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
        else:
            raise AssertionError(f"Unhandled rule evidence: {key}")

    if owner == "documents":
        document_id = "doc-1"
        request_status = "waived" if rule.target == "waived" else "accepted"
        db.execute(
            "INSERT INTO documents (id, application_id) VALUES (?, 'app-1')",
            (document_id,),
        )
        db.execute(
            """
            INSERT INTO application_enhanced_requirements
                (application_id, monitoring_alert_id, linked_document_id,
                 status, waiver_reason)
            VALUES ('app-1', ?, ?, ?, ?)
            """,
            (
                alert_id,
                document_id,
                request_status,
                "Senior-approved regulatory exception."
                if request_status == "waived"
                else None,
            ),
        )
        request_id = db.execute(
            "SELECT last_insert_rowid() AS id"
        ).fetchone()["id"]
        evidence["document_request_id"] = request_id
        if rule.target == "resolved":
            evidence["document_id"] = document_id
    db.commit()

    role = (
        "sco"
        if rule.target == "waived" or ("co" not in rule.roles and "sco" in rule.roles)
        else "co"
        if "co" in rule.roles
        else sorted(rule.roles)[0]
    )
    actor = dict(SENIOR if role == "sco" else OFFICER)
    actor["role"] = role
    actor_type = "officer" if "officer" in rule.actor_types else sorted(
        rule.actor_types
    )[0]
    workflow = sorted(rule.source_workflows)[0]
    reason_codes = {
        "triaged": "triage",
        "assigned": "assign",
        "in_review": (
            "senior_acknowledged"
            if rule.source == "escalated"
            else "review_started"
        ),
        "escalated": (
            "overdue_escalation"
            if "overdue_escalated" in rule.rule_id
            else "escalated_to_sco"
        ),
        "routed_to_review": "route_to_periodic_review",
        "routed_to_edd": "route_to_edd",
        "dismissed": "dismiss_false_positive",
        "resolved": (
            "document_accepted"
            if owner == "documents"
            else "no_material_impact"
        ),
        "waived": "document_waived",
    }
    return alert_id, actor, actor_type, workflow, reason_codes[rule.target], evidence


def test_exact_vocabulary_and_state_classes():
    assert sm.CANONICAL_STATUSES == (
        "open",
        "triaged",
        "assigned",
        "in_review",
        "escalated",
        "routed_to_review",
        "routed_to_edd",
        "dismissed",
        "resolved",
        "waived",
    )
    assert sm.ACTIVE_STATUSES == {
        "open", "triaged", "assigned", "in_review", "escalated"
    }
    assert sm.HANDOFF_STATUSES == {"routed_to_review", "routed_to_edd"}
    assert sm.TERMINAL_STATUSES == {"dismissed", "resolved", "waived"}


@pytest.mark.parametrize(
    "rule",
    sm.ENABLED_TRANSITION_RULES,
    ids=lambda rule: rule.rule_id,
)
def test_every_enabled_rule_succeeds_with_exact_evidence(state_db, rule):
    alert_id, actor, actor_type, workflow, reason_code, evidence = _rule_case(
        state_db, rule
    )
    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status=rule.source,
        target_status=rule.target,
        actor=actor,
        actor_type=actor_type,
        source_workflow=workflow,
        reason_code=reason_code,
        reason=f"Exercising {rule.rule_id}.",
        evidence=evidence,
        request_id="request-1",
    )
    assert result["status"] == rule.target
    assert result["transition_rule_id"] == rule.rule_id
    assert _status(state_db, alert_id) == rule.target
    rows = _audit_rows(state_db, alert_id)
    assert len(rows) == 1
    assert rows[0]["action"] == sm.CANONICAL_AUDIT_ACTION
    detail = json.loads(rows[0]["detail"])
    assert detail["previous_status"] == rule.source
    assert detail["new_status"] == rule.target
    assert detail["transition_matrix_version"] == sm.STATE_MACHINE_VERSION
    assert detail["transition_rule_id"] == rule.rule_id
    assert detail["originating_pr"] == sm.ORIGINATING_PR


def test_every_unlisted_status_pair_is_denied(state_db):
    listed_edges = {(rule.source, rule.target) for rule in sm.TRANSITION_RULES}
    for source in sm.CANONICAL_STATUSES:
        for target in sm.CANONICAL_STATUSES:
            if (source, target) in listed_edges:
                continue
            alert_id = _insert_alert(state_db, status=source)
            with pytest.raises(sm.MonitoringTransitionError):
                sm.transition_alert_status(
                    state_db,
                    alert_id,
                    expected_status=source,
                    target_status=target,
                    actor=OFFICER,
                    source_workflow="monitoring",
                    reason_code=next(
                        iter(sm.ALLOWED_REASON_CODES.get(target, {"triage"}))
                    ),
                    reason="This edge is not listed.",
                    evidence={},
                )
            assert _status(state_db, alert_id) == source
            assert _audit_rows(state_db, alert_id) == []


def test_unknown_and_legacy_statuses_fail_closed(state_db):
    alert_id = _insert_alert(state_db, status="Pending Review")
    with pytest.raises(sm.InvalidStatus):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="Pending Review",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Legacy value.",
            evidence={"source_reference": "test-source:1"},
        )
    assert _status(state_db, alert_id) == "Pending Review"


def test_missing_and_wrong_evidence_fail_closed(state_db):
    alert_id = _insert_alert(state_db)
    with pytest.raises(sm.MissingEvidence):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="No evidence.",
            evidence={},
        )
    with pytest.raises(sm.WrongEvidenceType):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Wrong evidence.",
            evidence={"source_reference": {"not": "scalar"}},
        )
    assert _status(state_db, alert_id) == "open"


def test_compliance_officer_cannot_assign_another_officer_via_service(state_db):
    alert_id = _insert_alert(state_db)

    with pytest.raises(sm.InsufficientRole, match="only self-assign"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="assigned",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="assign",
            reason="Attempt to bypass endpoint assignment controls.",
            evidence={"officer_id": "assigned-1"},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_senior_officer_can_assign_another_active_officer_via_service(state_db):
    alert_id = _insert_alert(state_db)

    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="open",
        target_status="assigned",
        actor=SENIOR,
        source_workflow="monitoring",
        reason_code="assign",
        reason="Senior assignment to an active Compliance Officer.",
        evidence={"officer_id": "assigned-1"},
    )

    assert result["status"] == "assigned"
    assert _status(state_db, alert_id) == "assigned"


def test_assignment_rejects_active_user_outside_officer_roles(state_db):
    state_db.execute(
        "INSERT INTO users (id, role, status) VALUES ('viewer-1', 'viewer', 'active')"
    )
    state_db.commit()
    alert_id = _insert_alert(state_db)

    with pytest.raises(sm.InsufficientRole, match="active CO, SCO, or Administrator"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="assigned",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="assign",
            reason="Invalid assignee role.",
            evidence={"officer_id": "viewer-1"},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_reason_code_is_bound_to_the_exact_transition_edge(state_db):
    alert_id = _insert_alert(state_db)

    with pytest.raises(sm.InvalidTransition, match="transition edge"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="in_review",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="senior_acknowledged",
            reason="This reason belongs only to escalated to in-review.",
            evidence={"source_reference": "test-source:1"},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_overdue_reason_requires_exact_escalation_ledger_evidence(state_db):
    alert_id = _insert_alert(state_db)

    with pytest.raises(sm.MissingEvidence, match="escalation_id"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="escalated",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="overdue_escalation",
            reason="Overdue claim without its canonical ledger.",
            evidence={"officer_rationale": "SLA was reported as overdue."},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_dismissal_reason_code_must_match_structured_reason(state_db):
    alert_id = _insert_alert(state_db, status="in_review")

    with pytest.raises(sm.InvalidTransition, match="does not match"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="dismiss_duplicate",
            reason="A mismatched coded decision must not be audited.",
            evidence={
                "dismissal_reason": "false_positive",
                "officer_rationale": "Evidence supports false positive only.",
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


@pytest.mark.parametrize(
    (
        "source_status",
        "target_status",
        "reason_code",
        "reason",
        "evidence",
        "actor",
    ),
    (
        (
            "open",
            "triaged",
            "triage",
            "Orphan triage.",
            {"source_reference": "test-source:1"},
            OFFICER,
        ),
        (
            "escalated",
            "in_review",
            "senior_acknowledged",
            "Orphan senior review.",
            {"officer_rationale": "Reviewed by senior."},
            SENIOR,
        ),
        (
            "in_review",
            "dismissed",
            "dismiss_no_action_needed",
            "Orphan dismissal.",
            {
                "dismissal_reason": "no_action_needed",
                "officer_rationale": "Cannot scope the orphan alert.",
            },
            OFFICER,
        ),
    ),
)
def test_orphan_alert_cannot_transition_without_application_linkage(
    state_db,
    source_status,
    target_status,
    reason_code,
    reason,
    evidence,
    actor,
):
    alert_id = _insert_alert(
        state_db,
        application_id=None,
        status=source_status,
    )
    with pytest.raises(sm.EvidenceLinkMismatch, match="no application linkage"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status=source_status,
            target_status=target_status,
            actor=actor,
            source_workflow="monitoring",
            reason_code=reason_code,
            reason=reason,
            evidence=evidence,
        )
    assert _status(state_db, alert_id) == source_status
    assert _audit_rows(state_db, alert_id) == []


def test_linked_object_must_exist_and_match_application(state_db):
    alert_id = _insert_alert(state_db, status="in_review")
    with pytest.raises(sm.LinkedObjectMissing):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="routed_to_edd",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="route_to_edd",
            reason="Route.",
            evidence={"edd_case_id": 999},
        )
    state_db.execute(
        "INSERT INTO edd_cases (application_id, stage) "
        "VALUES ('another-app', 'data_collection')"
    )
    edd_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()
    with pytest.raises(sm.EvidenceLinkMismatch):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="routed_to_edd",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="route_to_edd",
            reason="Route.",
            evidence={"edd_case_id": edd_id},
        )
    assert _status(state_db, alert_id) == "in_review"


@pytest.mark.parametrize(
    ("target_status", "reason_code", "evidence_key", "insert_sql"),
    (
        (
            "routed_to_edd",
            "route_to_edd",
            "edd_case_id",
            "INSERT INTO edd_cases "
            "(application_id, stage) VALUES ('app-1', 'data_collection')",
        ),
        (
            "routed_to_review",
            "route_to_periodic_review",
            "periodic_review_id",
            "INSERT INTO periodic_reviews "
            "(application_id, status) VALUES ('app-1', 'pending')",
        ),
    ),
)
def test_same_application_downstream_row_is_not_causal_linkage(
    state_db,
    target_status,
    reason_code,
    evidence_key,
    insert_sql,
):
    alert_id = _insert_alert(state_db, status="in_review")
    state_db.execute(insert_sql)
    linked_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="exact linkage"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status=target_status,
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code=reason_code,
            reason="Same application is not an alert-level link.",
            evidence={evidence_key: linked_id},
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_unlinked_document_request_is_not_causal_evidence(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        alert_type="document_expiring",
        detected_by="document_health",
    )
    state_db.execute(
        "INSERT INTO documents (id, application_id) VALUES ('doc-unlinked', 'app-1')"
    )
    state_db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, monitoring_alert_id, linked_document_id, status)
        VALUES ('app-1', NULL, 'doc-unlinked', 'accepted')
        """
    )
    request_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="not linked"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=OFFICER,
            source_workflow="kyc_documents",
            reason_code="document_accepted",
            reason="Same application is not an alert-level link.",
            evidence={
                "document_request_id": request_id,
                "document_id": "doc-unlinked",
            },
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_document_request_must_link_the_supplied_document(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        alert_type="document_expiring",
        detected_by="document_health",
    )
    state_db.execute(
        "INSERT INTO documents (id, application_id) VALUES ('doc-expected', 'app-1')"
    )
    state_db.execute(
        "INSERT INTO documents (id, application_id) VALUES ('doc-other', 'app-1')"
    )
    state_db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, monitoring_alert_id, linked_document_id, status)
        VALUES ('app-1', ?, 'doc-other', 'accepted')
        """,
        (alert_id,),
    )
    request_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="supplied document"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=OFFICER,
            source_workflow="kyc_documents",
            reason_code="document_accepted",
            reason="The request must link the exact accepted document.",
            evidence={
                "document_request_id": request_id,
                "document_id": "doc-expected",
            },
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_application_level_screening_review_is_not_causal_evidence(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        alert_type="sanctions_change",
        provider="complyadvantage",
        case_identifier="provider-case-1",
    )
    state_db.execute(
        "INSERT INTO screening_reviews (id, application_id, disposition) "
        "VALUES (31, 'app-1', 'cleared')"
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="no exact linkage"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="dismiss_false_positive",
            reason="Application context cannot identify the causal case.",
            evidence={
                "dismissal_reason": "false_positive",
                "officer_rationale": "Provider case reviewed.",
                "case_identifier": "provider-case-1",
                "screening_case_id": 31,
            },
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_document_transition_is_rejected_for_manual_alert(state_db):
    alert_id = _insert_alert(state_db, status="in_review", alert_type="manual")
    state_db.execute(
        "INSERT INTO documents (id, application_id) VALUES ('doc-manual', 'app-1')"
    )
    state_db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, monitoring_alert_id, linked_document_id, status)
        VALUES ('app-1', NULL, 'doc-manual', 'accepted')
        """
    )
    request_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()
    with pytest.raises(sm.WrongAlertType):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=OFFICER,
            source_workflow="kyc_documents",
            reason_code="document_accepted",
            reason="Wrong owner.",
            evidence={
                "document_request_id": request_id,
                "document_id": "doc-manual",
            },
        )
    assert _status(state_db, alert_id) == "in_review"


def test_downstream_owned_link_cannot_be_dismissed_by_monitoring(state_db):
    state_db.execute(
        "INSERT INTO edd_cases (application_id, stage) "
        "VALUES ('app-1', 'data_collection')"
    )
    edd_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        linked_edd_case_id=edd_id,
    )
    with pytest.raises(sm.WrongAlertType):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="dismiss_no_action_needed",
            reason="Monitoring cannot decide an EDD-owned outcome.",
            evidence={
                "dismissal_reason": "no_action_needed",
                "officer_rationale": "EDD owns the linked decision.",
            },
        )
    assert _status(state_db, alert_id) == "in_review"


@pytest.mark.parametrize(
    ("owner_table", "insert_sql"),
    (
        (
            "edd",
            "INSERT INTO edd_cases "
            "(application_id, stage, linked_monitoring_alert_id) "
            "VALUES ('app-1', 'data_collection', ?)",
        ),
        (
            "periodic",
            "INSERT INTO periodic_reviews "
            "(application_id, status, linked_monitoring_alert_id) "
            "VALUES ('app-1', 'pending', ?)",
        ),
    ),
)
def test_reverse_downstream_link_is_authoritative(
    state_db,
    owner_table,
    insert_sql,
):
    alert_id = _insert_alert(state_db, status="in_review")
    state_db.execute(insert_sql, (alert_id,))
    state_db.commit()

    expected_owner = "edd" if owner_table == "edd" else "periodic_review"
    alert = dict(
        state_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    )
    assert sm.alert_owner(state_db, alert) == expected_owner
    with pytest.raises(sm.WrongAlertType):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="dismiss_no_action_needed",
            reason="Monitoring cannot decide a downstream-owned outcome.",
            evidence={
                "dismissal_reason": "no_action_needed",
                "officer_rationale": "The linked workflow owns the decision.",
            },
        )
    assert _status(state_db, alert_id) == "in_review"


def test_change_management_bridge_is_authoritative(state_db):
    alert_id = _insert_alert(state_db, status="in_review")
    state_db.execute(
        "INSERT INTO change_alerts (id, application_id, source_reference) "
        "VALUES ('ca-1', 'app-1', ?)",
        (f"monitoring_alert:{alert_id}",),
    )
    state_db.execute(
        "INSERT INTO change_requests (id, application_id, source_alert_id) "
        "VALUES ('cr-1', 'app-1', 'ca-1')"
    )
    state_db.commit()
    alert = dict(
        state_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    )
    assert sm.alert_owner(state_db, alert) == "change_management"
    with pytest.raises(sm.WrongAlertType):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Change Management owns the linked decision.",
            evidence={"officer_rationale": "No Monitoring decision is permitted."},
        )
    assert _status(state_db, alert_id) == "in_review"


def test_exact_screening_evidence_link_is_authoritative_for_generic_alert(
    state_db,
):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        alert_type="other",
        provider=None,
        case_identifier=None,
    )
    state_db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier)
        VALUES (?, 'app-1', 'complyadvantage', 'provider-case-generic')
        """,
        (alert_id,),
    )
    state_db.commit()

    alert = dict(
        state_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    )
    assert sm.alert_owner(state_db, alert) == "screening_review"
    with pytest.raises(sm.WrongAlertType):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Screening Review owns this exact evidence link.",
            evidence={"officer_rationale": "Manual resolution is prohibited."},
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_screening_evidence_application_conflict_fails_closed(state_db):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="other",
    )
    state_db.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier)
        VALUES (?, 'another-app', 'complyadvantage', 'provider-case-conflict')
        """,
        (alert_id,),
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="application linkage"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Conflicting evidence ownership.",
            evidence={"source_reference": "test-source:1"},
        )
    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_conflicting_explicit_owners_fail_closed(state_db):
    alert_id = _insert_alert(state_db, status="in_review")
    state_db.execute(
        "INSERT INTO edd_cases "
        "(application_id, stage, linked_monitoring_alert_id) "
        "VALUES ('app-1', 'data_collection', ?)",
        (alert_id,),
    )
    state_db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, monitoring_alert_id, status)
        VALUES ('app-1', ?, 'under_review')
        """,
        (alert_id,),
    )
    state_db.commit()
    with pytest.raises(sm.AmbiguousAlertOwner):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="dismiss_no_action_needed",
            reason="Conflicting owner links.",
            evidence={
                "dismissal_reason": "no_action_needed",
                "officer_rationale": "This must fail closed.",
            },
        )
    assert _status(state_db, alert_id) == "in_review"


def test_multiple_document_requirement_links_fail_closed(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        alert_type="document_expiring",
        detected_by="document_health",
    )
    state_db.execute(
        "INSERT INTO application_enhanced_requirements "
        "(application_id, monitoring_alert_id, status) "
        "VALUES ('app-1', ?, 'under_review')",
        (alert_id,),
    )
    state_db.execute(
        "INSERT INTO application_enhanced_requirements "
        "(application_id, monitoring_alert_id, status) "
        "VALUES ('app-1', ?, 'accepted')",
        (alert_id,),
    )
    state_db.commit()

    with pytest.raises(sm.AmbiguousAlertOwner, match="multiple linked"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="dismiss_no_action_needed",
            reason="Duplicate document links must fail closed.",
            evidence={
                "dismissal_reason": "no_action_needed",
                "officer_rationale": "Linkage requires reconciliation.",
            },
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_cross_application_document_requirement_link_fails_closed(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        alert_type="document_expiring",
        detected_by="document_health",
    )
    state_db.execute(
        "INSERT INTO application_enhanced_requirements "
        "(application_id, monitoring_alert_id, status) "
        "VALUES ('app-2', ?, 'under_review')",
        (alert_id,),
    )
    state_db.commit()

    alert = dict(
        state_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    )
    with pytest.raises(sm.EvidenceLinkMismatch, match="application linkage"):
        sm.alert_owner(state_db, alert)
    assert _status(state_db, alert_id) == "in_review"


@pytest.mark.parametrize(
    ("alert_link_column", "table_name", "row_state_column", "row_state"),
    (
        ("linked_edd_case_id", "edd_cases", "stage", "data_collection"),
        (
            "linked_periodic_review_id",
            "periodic_reviews",
            "status",
            "pending",
        ),
    ),
)
def test_conflicting_direct_and_reverse_links_for_same_owner_fail_closed(
    state_db,
    alert_link_column,
    table_name,
    row_state_column,
    row_state,
):
    state_db.execute(
        f"INSERT INTO {table_name} (application_id, {row_state_column}) "
        "VALUES ('app-1', ?)",
        (row_state,),
    )
    direct_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        **{alert_link_column: direct_id},
    )
    state_db.execute(
        f"INSERT INTO {table_name} "
        f"(application_id, {row_state_column}, linked_monitoring_alert_id) "
        "VALUES ('app-1', ?, ?)",
        (row_state, alert_id),
    )
    state_db.commit()

    alert = dict(
        state_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    )
    with pytest.raises(sm.AmbiguousAlertOwner, match="multiple linked"):
        sm.alert_owner(state_db, alert)
    assert _status(state_db, alert_id) == "in_review"


def test_direct_downstream_row_pointing_to_another_alert_fails_closed(state_db):
    other_alert_id = _insert_alert(state_db, status="in_review")
    state_db.execute(
        "INSERT INTO edd_cases "
        "(application_id, stage, linked_monitoring_alert_id) "
        "VALUES ('app-1', 'data_collection', ?)",
        (other_alert_id,),
    )
    edd_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        linked_edd_case_id=edd_id,
    )

    alert = dict(
        state_db.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
    )
    with pytest.raises(sm.EvidenceLinkMismatch, match="another Monitoring Alert"):
        sm.alert_owner(state_db, alert)
    assert _status(state_db, alert_id) == "in_review"


def test_actor_role_and_system_actor_are_restricted(state_db):
    alert_id = _insert_alert(state_db)
    with pytest.raises(sm.InsufficientRole):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor={"sub": "analyst-1", "name": "Analyst", "role": "analyst"},
            source_workflow="monitoring",
            reason_code="triage",
            reason="Unauthorised.",
            evidence={"source_reference": "test-source:1"},
        )
    with pytest.raises(sm.InsufficientRole):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor={"id": "scheduler", "role": "system"},
            actor_type="system",
            source_workflow="monitoring",
            reason_code="triage",
            reason="Automation remains disabled.",
            evidence={"source_reference": "test-source:1"},
        )
    assert _status(state_db, alert_id) == "open"


def test_document_outcome_cannot_be_automated_by_a_system_actor(state_db):
    alert_id = _insert_alert(
        state_db,
        alert_type="document_expiring",
        detected_by="document_health",
    )
    state_db.execute(
        "INSERT INTO documents (id, application_id) VALUES ('doc-system', 'app-1')"
    )
    state_db.execute(
        "INSERT INTO application_enhanced_requirements "
        "(application_id, monitoring_alert_id, linked_document_id, status) "
        "VALUES ('app-1', ?, 'doc-system', 'accepted')",
        (alert_id,),
    )
    request_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()

    with pytest.raises(sm.InsufficientRole):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="resolved",
            actor={"id": "document-agent", "role": "system"},
            actor_type="downstream",
            source_workflow="kyc_documents",
            reason_code="document_accepted",
            reason="Automation must remain inactive.",
            evidence={
                "document_request_id": request_id,
                "document_id": "doc-system",
            },
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_future_downstream_transition_is_defined_but_disabled(state_db):
    state_db.execute(
        "INSERT INTO edd_cases (application_id, stage) "
        "VALUES ('app-1', 'edd_approved')"
    )
    edd_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    alert_id = _insert_alert(
        state_db,
        status="routed_to_edd",
        linked_edd_case_id=edd_id,
    )
    with pytest.raises(sm.FutureTransitionDisabled):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="routed_to_edd",
            target_status="resolved",
            actor={"id": "edd-sync", "role": "system"},
            actor_type="downstream",
            source_workflow="edd",
            reason_code="downstream_outcome",
            reason="Consumer is not active.",
            evidence={
                "edd_case_id": edd_id,
                "downstream_outcome": "edd_approved",
            },
        )
    assert _status(state_db, alert_id) == "routed_to_edd"


def test_terminal_state_cannot_reopen(state_db):
    alert_id = _insert_alert(state_db, status="resolved")
    with pytest.raises(sm.TerminalStateError):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="resolved",
            target_status="triaged",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Reopen attempt.",
            evidence={"source_reference": "test-source:1"},
        )
    assert _status(state_db, alert_id) == "resolved"


def test_terminal_transition_replaces_stale_resolved_timestamp(state_db):
    alert_id = _insert_alert(state_db, status="in_review")
    stale_timestamp = "2020-01-01 00:00:00"
    state_db.execute(
        "UPDATE monitoring_alerts SET resolved_at = ? WHERE id = ?",
        (stale_timestamp, alert_id),
    )
    state_db.commit()

    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="in_review",
        target_status="resolved",
        actor=OFFICER,
        source_workflow="monitoring",
        reason_code="no_material_impact",
        reason="The current terminal decision owns the terminal timestamp.",
        evidence={"officer_rationale": "No material impact after review."},
    )

    assert result["status"] == "resolved"
    resolved_at = state_db.execute(
        "SELECT resolved_at FROM monitoring_alerts WHERE id = ?",
        (alert_id,),
    ).fetchone()["resolved_at"]
    assert resolved_at not in (None, "", stale_timestamp)


def test_stale_source_and_duplicate_request_create_no_audit(state_db):
    alert_id = _insert_alert(state_db)
    sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="open",
        target_status="triaged",
        actor=OFFICER,
        source_workflow="monitoring",
        reason_code="triage",
        reason="First request.",
        evidence={"source_reference": "test-source:1"},
    )
    with pytest.raises(sm.AlreadyInTargetState):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Duplicate request.",
            evidence={"source_reference": "test-source:1"},
        )
    with pytest.raises(sm.StaleCurrentState):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="assigned",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="assign",
            reason="Stale request.",
            evidence={"officer_id": "assigned-1"},
        )
    assert len(_audit_rows(state_db, alert_id)) == 1


def test_audit_failure_rolls_back_status_update(state_db, monkeypatch):
    alert_id = _insert_alert(state_db)

    def broken_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(sm, "_canonical_audit_append", lambda: broken_audit)
    with pytest.raises(sm.AuditInfrastructureError):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Must roll back.",
            evidence={"source_reference": "test-source:1"},
        )
    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_silent_noop_audit_writer_cannot_commit_an_unaudited_transition(
    state_db,
    monkeypatch,
):
    alert_id = _insert_alert(state_db)
    monkeypatch.setattr(
        sm,
        "_canonical_audit_append",
        lambda: (lambda *_args, **_kwargs: "fabricated-hash"),
    )

    with pytest.raises(sm.AuditInfrastructureError):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="A no-op audit writer must fail closed.",
            evidence={"source_reference": "test-source:1"},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_missing_request_id_gets_a_recorded_transition_correlation_id(state_db):
    alert_id = _insert_alert(state_db)

    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="open",
        target_status="triaged",
        actor=OFFICER,
        source_workflow="monitoring",
        reason_code="triage",
        reason="Transition without an inbound HTTP request.",
        evidence={"source_reference": "test-source:1"},
    )

    row = _audit_rows(state_db, alert_id)[0]
    detail = json.loads(row["detail"])
    assert result["status"] == "triaged"
    assert detail["request_id"].startswith("monitoring-transition-")
    assert row["request_id"] == detail["request_id"]


def test_update_failure_creates_no_audit(state_db, monkeypatch):
    alert_id = _insert_alert(state_db)
    state_db.execute(
        """
        CREATE TRIGGER block_monitoring_status_update
        BEFORE UPDATE OF status ON monitoring_alerts
        BEGIN
            SELECT RAISE(ABORT, 'blocked update');
        END
        """
    )
    state_db.commit()
    called = []

    def audit_spy(*args, **kwargs):
        called.append(True)
        return "should-not-exist"

    monkeypatch.setattr(sm, "_canonical_audit_append", lambda: audit_spy)
    with pytest.raises(sqlite3.IntegrityError):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Blocked update.",
            evidence={"source_reference": "test-source:1"},
        )
    assert called == []
    assert _status(state_db, alert_id) == "open"


def test_caller_owned_transaction_can_roll_back_status_and_audit(state_db):
    alert_id = _insert_alert(state_db)
    sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="open",
        target_status="triaged",
        actor=OFFICER,
        source_workflow="monitoring",
        reason_code="triage",
        reason="Caller transaction.",
        evidence={"source_reference": "test-source:1"},
        commit=False,
    )
    assert _status(state_db, alert_id) == "triaged"
    assert len(_audit_rows(state_db, alert_id)) == 1
    state_db.rollback()
    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_material_clear_requires_approved_four_eyes_record(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    with pytest.raises(sm.FourEyesRequired):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Material clear.",
            evidence={"officer_rationale": "Senior evidence assessment."},
        )
    review_id = _insert_review_control(state_db, alert_id)
    state_db.commit()
    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="in_review",
        target_status="resolved",
        actor=SENIOR,
        source_workflow="monitoring",
        reason_code="no_material_impact",
        reason="Material clear after independent review.",
        evidence={
            "officer_rationale": "Senior evidence assessment.",
            "review_request_id": review_id,
        },
    )
    assert result["status"] == "resolved"


def test_controlled_document_accept_cannot_bypass_review_ledger(state_db):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="document_expired",
        detected_by="document_health",
        summary="The client's passport is expired.",
    )
    request_id, document_id = _insert_document_requirement(state_db, alert_id)

    with pytest.raises(sm.FourEyesRequired, match="review-control"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="kyc_documents",
            reason_code="document_accepted",
            reason="Direct service calls must not bypass document control.",
            evidence={
                "document_request_id": request_id,
                "document_id": document_id,
                "officer_rationale": "The replacement passport was reviewed.",
            },
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_controlled_document_waiver_cannot_bypass_review_ledger(state_db):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="document_expired",
        detected_by="document_health",
        summary="The client's passport is expired.",
    )
    waiver_reason = "Documented temporary regulatory exception."
    request_id, _document_id = _insert_document_requirement(
        state_db,
        alert_id,
        status="waived",
        waiver_reason=waiver_reason,
    )

    with pytest.raises(sm.FourEyesRequired, match="review-control"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="waived",
            actor=SENIOR,
            source_workflow="kyc_documents",
            reason_code="document_waived",
            reason="Direct service calls must not bypass waiver control.",
            evidence={
                "document_request_id": request_id,
                "waiver_reason": waiver_reason,
                "officer_rationale": waiver_reason,
            },
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


@pytest.mark.parametrize(
    ("review_state", "initiated_by", "second_review_bypassed"),
    (
        ("approved", "officer-1", 0),
        ("senior_cleared", "senior-1", 1),
    ),
)
@pytest.mark.parametrize(
    ("requested_outcome", "reason_code"),
    (
        ("accept_updated_document", "document_accepted"),
        ("mark_already_updated", "document_already_updated"),
    ),
)
def test_exact_document_review_control_authorizes_transition(
    state_db,
    review_state,
    initiated_by,
    second_review_bypassed,
    requested_outcome,
    reason_code,
):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="document_expired",
        detected_by="document_health",
        summary="The client's passport is expired.",
    )
    request_id, document_id = _insert_document_requirement(state_db, alert_id)
    rationale = "The replacement passport was reviewed and accepted."
    review_id = _insert_review_control(
        state_db,
        alert_id,
        state=review_state,
        initiated_by=initiated_by,
        approved_by="senior-1",
        source_status="open",
        requested_outcome=requested_outcome,
        rationale=rationale,
        evidence_ref="Exact replacement-document review evidence.",
        transition_evidence={
            "document_request_id": request_id,
            "document_id": document_id,
        },
        tier=2,
        second_review_bypassed=second_review_bypassed,
    )
    state_db.commit()

    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="open",
        target_status="resolved",
        actor=SENIOR,
        source_workflow="kyc_documents",
        reason_code=reason_code,
        reason=rationale,
        evidence={
            "document_request_id": request_id,
            "document_id": document_id,
            "officer_rationale": rationale,
            "review_request_id": review_id,
        },
    )

    assert result["status"] == "resolved"
    assert len(_audit_rows(state_db, alert_id)) == 1


@pytest.mark.parametrize(
    "stored_outcome",
    ("mark_already_updated", "Accept-Updated-Document"),
)
def test_document_review_control_is_bound_to_exact_outcome(
    state_db,
    stored_outcome,
):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="document_expired",
        detected_by="document_health",
        summary="The client's passport is expired.",
    )
    request_id, document_id = _insert_document_requirement(state_db, alert_id)
    rationale = "The replacement passport was reviewed."
    review_id = _insert_review_control(
        state_db,
        alert_id,
        source_status="open",
        requested_outcome=stored_outcome,
        rationale=rationale,
        evidence_ref="Exact replacement-document review evidence.",
        transition_evidence={
            "document_request_id": request_id,
            "document_id": document_id,
        },
        tier=2,
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="different terminal outcome"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="kyc_documents",
            reason_code="document_accepted",
            reason=rationale,
            evidence={
                "document_request_id": request_id,
                "document_id": document_id,
                "officer_rationale": rationale,
                "review_request_id": review_id,
            },
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


@pytest.mark.parametrize(
    ("review_state", "initiated_by", "second_review_bypassed"),
    (
        ("approved", "officer-1", 0),
        ("senior_cleared", "senior-1", 1),
    ),
)
def test_exact_document_waiver_control_authorizes_transition(
    state_db,
    review_state,
    initiated_by,
    second_review_bypassed,
):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="document_expired",
        detected_by="document_health",
        summary="The client's passport is expired.",
    )
    rationale = "Documented temporary regulatory exception."
    request_id, _document_id = _insert_document_requirement(
        state_db,
        alert_id,
        status="waived",
        waiver_reason=rationale,
    )
    review_id = _insert_review_control(
        state_db,
        alert_id,
        state=review_state,
        initiated_by=initiated_by,
        approved_by="senior-1",
        source_status="open",
        requested_outcome="waive_with_reason",
        rationale=rationale,
        evidence_ref="Exact waiver evidence.",
        transition_evidence={"document_request_id": request_id},
        tier=2,
        second_review_bypassed=second_review_bypassed,
    )
    state_db.commit()

    result = sm.transition_alert_status(
        state_db,
        alert_id,
        expected_status="open",
        target_status="waived",
        actor=SENIOR,
        source_workflow="kyc_documents",
        reason_code="document_waived",
        reason=rationale,
        evidence={
            "document_request_id": request_id,
            "waiver_reason": rationale,
            "officer_rationale": rationale,
            "review_request_id": review_id,
        },
    )

    assert result["status"] == "waived"
    assert len(_audit_rows(state_db, alert_id)) == 1


def test_oversized_text_evidence_is_rejected_without_mutation(state_db):
    alert_id = _insert_alert(state_db, status="open")

    with pytest.raises(sm.WrongEvidenceType, match="1000-character limit"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Oversized evidence must fail explicitly.",
            evidence={"source_reference": "x" * 1001},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_oversized_transition_reason_is_rejected_without_mutation(state_db):
    alert_id = _insert_alert(state_db, status="open")

    with pytest.raises(sm.InvalidTransition, match="1000-character limit"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="x" * 1001,
            evidence={"source_reference": "test-source"},
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_review_control_with_blank_maker_cannot_authorize_transition(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    review_id = _insert_review_control(state_db, alert_id)
    state_db.execute(
        "UPDATE monitoring_alert_review_requests SET initiated_by = '' "
        "WHERE id = ?",
        (review_id,),
    )
    state_db.commit()

    with pytest.raises(sm.FourEyesRequired, match="maker/checker"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Blank maker must fail closed.",
            evidence={
                "officer_rationale": "Senior evidence assessment.",
                "review_request_id": review_id,
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_review_control_revalidates_current_material_evidence(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    review_id = _insert_review_control(
        state_db,
        alert_id,
        evidence_ref="",
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="documented evidence"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Evidence must be revalidated by the canonical service.",
            evidence={
                "officer_rationale": "Senior evidence assessment.",
                "review_request_id": review_id,
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


@pytest.mark.parametrize(
    ("source_status", "requested_outcome", "match"),
    (
        ("open", "no_material_impact", "exact current source status"),
        ("in_review", "dismiss", "different terminal outcome"),
    ),
)
def test_review_control_must_match_source_state_and_terminal_outcome(
    state_db,
    source_status,
    requested_outcome,
    match,
):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    review_id = _insert_review_control(
        state_db,
        alert_id,
        source_status=source_status,
        requested_outcome=requested_outcome,
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match=match):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="The approved intent must be exact.",
            evidence={
                "officer_rationale": "Senior evidence assessment.",
                "review_request_id": review_id,
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_review_control_must_match_dismissal_reason_and_rationale(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    wrong_reason_id = _insert_review_control(
        state_db,
        alert_id,
        requested_outcome="dismiss",
        dismissal_reason="duplicate",
        rationale="Approved false-positive rationale.",
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="dismissal reason"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="dismiss_false_positive",
            reason="Wrong stored dismissal intent.",
            evidence={
                "dismissal_reason": "false_positive",
                "officer_rationale": "Approved false-positive rationale.",
                "review_request_id": wrong_reason_id,
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []

    state_db.execute(
        "UPDATE monitoring_alert_review_requests "
        "SET dismissal_reason = 'false_positive', rationale = 'Different rationale' "
        "WHERE id = ?",
        (wrong_reason_id,),
    )
    state_db.commit()
    with pytest.raises(sm.EvidenceLinkMismatch, match="rationale"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="dismiss_false_positive",
            reason="Wrong stored rationale.",
            evidence={
                "dismissal_reason": "false_positive",
                "officer_rationale": "Approved false-positive rationale.",
                "review_request_id": wrong_reason_id,
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_review_control_typed_evidence_must_match_exactly(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
        alert_type="sanctions_change",
        provider="complyadvantage",
        case_identifier="case-exact",
    )
    review_id = _insert_review_control(
        state_db,
        alert_id,
        requested_outcome="dismiss",
        dismissal_reason="false_positive",
        rationale="Exact screening disposition rationale.",
        transition_evidence={"case_identifier": "case-other"},
    )
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="typed evidence"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="dismissed",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="dismiss_false_positive",
            reason="Typed evidence mismatch.",
            evidence={
                "dismissal_reason": "false_positive",
                "officer_rationale": "Exact screening disposition rationale.",
                "case_identifier": "case-exact",
                "review_request_id": review_id,
            },
        )

    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_document_waiver_reason_must_match_stored_approved_reason(state_db):
    alert_id = _insert_alert(
        state_db,
        status="open",
        alert_type="document_expiring",
        detected_by="document_health",
    )
    state_db.execute(
        "INSERT INTO application_enhanced_requirements "
        "(application_id, monitoring_alert_id, status, waiver_reason) "
        "VALUES ('app-1', ?, 'waived', 'Approved regulatory exception.')",
        (alert_id,),
    )
    request_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()

    with pytest.raises(sm.EvidenceLinkMismatch, match="waiver_reason"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="open",
            target_status="waived",
            actor=SENIOR,
            source_workflow="kyc_documents",
            reason_code="document_waived",
            reason="Invented audit reason must not replace approved evidence.",
            evidence={
                "document_request_id": request_id,
                "waiver_reason": "Different invented exception.",
            },
        )

    assert _status(state_db, alert_id) == "open"
    assert _audit_rows(state_db, alert_id) == []


def test_self_approved_review_control_is_rejected(state_db):
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    state_db.execute(
        "INSERT INTO monitoring_alert_review_requests "
        "(alert_id, state, initiated_by, approved_by) "
        "VALUES (?, 'approved', 'senior-1', 'senior-1')",
        (alert_id,),
    )
    review_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()
    with pytest.raises(sm.FourEyesRequired, match="maker-checker"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Invalid self-approved disposition.",
            evidence={
                "officer_rationale": "Self-approved rationale.",
                "review_request_id": review_id,
            },
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def test_fabricated_co_senior_clear_record_is_rejected(state_db):
    """Canonical transitions re-check the ledger approver's database role."""
    alert_id = _insert_alert(
        state_db,
        status="in_review",
        severity="critical",
    )
    state_db.execute(
        "INSERT INTO monitoring_alert_review_requests "
        "(alert_id, state, initiated_by, approved_by) "
        "VALUES (?, 'senior_cleared', 'officer-1', 'officer-1')",
        (alert_id,),
    )
    review_id = state_db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]
    state_db.commit()

    with pytest.raises(sm.FourEyesRequired, match="authoritative active senior"):
        sm.transition_alert_status(
            state_db,
            alert_id,
            expected_status="in_review",
            target_status="resolved",
            actor=SENIOR,
            source_workflow="monitoring",
            reason_code="no_material_impact",
            reason="Fabricated senior-clear record must not authorize.",
            evidence={
                "officer_rationale": "Controlled direct-service regression.",
                "review_request_id": review_id,
            },
        )
    assert _status(state_db, alert_id) == "in_review"
    assert _audit_rows(state_db, alert_id) == []


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


@pytest.mark.skipif(not _postgres_dsn(), reason="No disposable PostgreSQL test DSN")
def test_postgres_row_lock_serializes_competing_transitions():
    dsn = _postgres_dsn()
    parts = urlsplit(dsn)
    if parts.hostname not in (None, "localhost", "127.0.0.1", "::1"):
        pytest.skip("Concurrency test requires a local disposable PostgreSQL server")
    if "test" not in (parts.path or "").lower():
        pytest.skip("Concurrency test requires a test-named PostgreSQL database")

    import psycopg2

    schema = f"monitoring_sm_{uuid.uuid4().hex[:12]}"
    raw1 = psycopg2.connect(dsn)
    raw2 = psycopg2.connect(dsn)
    db1 = DBConnection(raw1, is_postgres=True, database_identity=dsn)
    db2 = DBConnection(raw2, is_postgres=True, database_identity=dsn)

    try:
        db1.execute(f'CREATE SCHEMA "{schema}"')
        db1.execute(f'SET search_path TO "{schema}"')
        db1.execute(
            """
            CREATE TABLE monitoring_alerts (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                provider TEXT,
                case_identifier TEXT,
                alert_type TEXT,
                severity TEXT,
                detected_by TEXT,
                summary TEXT,
                source_reference TEXT,
                status TEXT NOT NULL,
                linked_periodic_review_id INTEGER,
                linked_edd_case_id INTEGER,
                triaged_at TIMESTAMP,
                assigned_at TIMESTAMP,
                resolved_at TIMESTAMP
            )
            """
        )
        db1.execute(
            """
            CREATE TABLE audit_log (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP,
                user_id TEXT,
                user_name TEXT,
                user_role TEXT,
                action TEXT NOT NULL,
                target TEXT,
                application_id TEXT,
                detail TEXT,
                ip_address TEXT,
                request_id TEXT,
                before_state TEXT,
                after_state TEXT,
                previous_hash TEXT,
                entry_hash TEXT
            )
            """
        )
        db1.execute(
            "CREATE UNIQUE INDEX audit_log_previous_hash_unique "
            "ON audit_log(previous_hash) WHERE previous_hash IS NOT NULL"
        )
        db1.execute(
            "CREATE TABLE users "
            "(id TEXT PRIMARY KEY, role TEXT NOT NULL, status TEXT NOT NULL)"
        )
        db1.execute(
            "CREATE TABLE edd_cases "
            "(id SERIAL PRIMARY KEY, application_id TEXT, "
            "linked_monitoring_alert_id INTEGER)"
        )
        db1.execute(
            "CREATE TABLE periodic_reviews "
            "(id SERIAL PRIMARY KEY, application_id TEXT, "
            "linked_monitoring_alert_id INTEGER)"
        )
        db1.execute(
            "CREATE TABLE change_alerts "
            "(id TEXT PRIMARY KEY, application_id TEXT, source_reference TEXT)"
        )
        db1.execute(
            "CREATE TABLE change_requests "
            "(id TEXT PRIMARY KEY, application_id TEXT, source_alert_id TEXT)"
        )
        db1.execute(
            "CREATE TABLE application_enhanced_requirements "
            "(id SERIAL PRIMARY KEY, application_id TEXT, "
            "monitoring_alert_id INTEGER)"
        )
        db1.execute(
            "CREATE TABLE monitoring_alert_evidence "
            "(id SERIAL PRIMARY KEY, monitoring_alert_id INTEGER NOT NULL, "
            "application_id TEXT, provider TEXT NOT NULL, case_identifier TEXT)"
        )
        db1.execute(
            "INSERT INTO users (id, role, status) "
            "VALUES ('officer-1', 'co', 'active')"
        )
        db1.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, alert_type, severity, source_reference, status)
            VALUES ('app-1', 'manual', 'medium', 'test-source:1', 'open')
            """
        )
        db1.commit()

        db2.execute(f'SET search_path TO "{schema}"')
        db2.execute("SET lock_timeout = '250ms'")
        db2.commit()

        sm.transition_alert_status(
            db1,
            1,
            expected_status="open",
            target_status="triaged",
            actor=OFFICER,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Winning transition.",
            evidence={"source_reference": "test-source:1"},
            commit=False,
        )

        with pytest.raises(psycopg2.errors.LockNotAvailable):
            sm.transition_alert_status(
                db2,
                1,
                expected_status="open",
                target_status="assigned",
                actor=OFFICER,
                source_workflow="monitoring",
                reason_code="assign",
                reason="Competing transition.",
                evidence={"officer_id": "assigned-1"},
            )

        db1.commit()
        with pytest.raises(sm.AlreadyInTargetState):
            sm.transition_alert_status(
                db2,
                1,
                expected_status="open",
                target_status="triaged",
                actor=OFFICER,
                source_workflow="monitoring",
                reason_code="triage",
                reason="Duplicate transition.",
                evidence={"source_reference": "test-source:1"},
            )
        assert db1.execute(
            "SELECT COUNT(*) AS count FROM audit_log "
            "WHERE action = 'monitoring.alert.status_transition'"
        ).fetchone()["count"] == 1
    finally:
        for db in (db2, db1):
            try:
                db.rollback()
            except Exception:
                pass
        try:
            db1.execute("SET search_path TO public")
            db1.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            db1.commit()
        finally:
            db2.close()
            db1.close()
