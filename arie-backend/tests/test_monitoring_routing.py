"""
Tests for monitoring_routing primitives -- PR-02.

Verifies:

* triage_alert / assign_alert update both the lifecycle timestamp and
  the public officer-facing status, and emit a routing audit event;
* dismiss_alert requires a structured reason from the whitelist,
  records who/when/why, and refuses to dismiss an already-terminal alert;
* route_alert_to_periodic_review:
    - creates a real periodic_reviews row,
    - links it bidirectionally via PR-01 helpers,
    - sets trigger_source='monitoring_alert' on the review,
    - on a second call for the same alert, REUSES the linked review
      and does NOT create a duplicate row;
* route_alert_to_edd:
    - creates a real edd_cases row when there is no active case,
    - sets origin_context='monitoring_alert' on the EDD,
    - reuses an already-linked active EDD case on repeat calls,
    - reuses any other active EDD case for the same application
      (no duplicate active EDD ever created from monitoring),
    - creates a fresh case when the previously linked one is terminal;
* every routing action emits an audit event reachable from the
  injected audit_writer (which mirrors BaseHandler.log_audit).

Test fixtures intentionally mirror tests/test_lifecycle_linkage.py so
that PR-01 and PR-02 share schema setup behaviour.
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def routing_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with repository schema + migration 008 applied."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    import importlib
    import db as db_module
    importlib.reload(db_module)
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
    db_module.init_db()
    conn = db_module.get_db()

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "version TEXT UNIQUE NOT NULL, "
        "filename TEXT NOT NULL, "
        "description TEXT DEFAULT '', "
        "applied_at TEXT DEFAULT (datetime('now')), "
        "checksum TEXT)"
    )
    _PRE_APPLIED = [
        ("001", "migration_001_initial.sql"),
        ("002", "migration_002_supervisor_tables.sql"),
        ("003", "migration_003_monitoring_indexes.sql"),
        ("004", "migration_004_documents_s3_key.sql"),
        ("005", "migration_005_applications_truth_schema.sql"),
        ("006", "migration_006_person_dob.sql"),
        ("007", "migration_007_screening_reports_normalized.sql"),
    ]
    for _v, _fn in _PRE_APPLIED:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?, ?)",
            (_v, _fn),
        )
    conn.commit()

    try:
        conn.execute(
            "INSERT INTO applications (id, status) VALUES (?, ?)",
            ("test-app-100", "approved"),
        )
    except Exception:
        conn.execute(
            "INSERT OR IGNORE INTO applications (id) VALUES (?)",
            ("test-app-100",),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO users
            (id, email, password_hash, full_name, role, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "officer-1",
            "officer-1@example.test",
            "not-a-login-secret",
            "Test Officer",
            "co",
            "active",
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO users
            (id, email, password_hash, full_name, role, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "sco-9",
            "sco-9@example.test",
            "not-a-login-secret",
            "Senior Officer",
            "sco",
            "active",
        ),
    )
    conn.commit()

    from migrations.runner import run_all_migrations_with_connection
    run_all_migrations_with_connection(conn)

    yield conn
    conn.close()


@pytest.fixture
def audit_sink():
    events = []

    def writer(user, action, target, detail, db=None,
               before_state=None, after_state=None, commit=True):
        # RDI-007: routing actions now write audit rows with commit=False so
        # they join the caller's open transaction. Record `commit` so tests can
        # assert the audit joined the transaction (was not independently
        # committed) rather than being flushed early.
        events.append({
            "user": dict(user) if user else {},
            "action": action,
            "target": target,
            "detail": detail,
            "before_state": before_state,
            "after_state": after_state,
            "commit": commit,
        })

    writer.events = events
    return writer


def _insert_alert(conn, *, application_id="test-app-100",
                  client_name="Client A", status="open",
                  severity="medium", alert_type="manual"):
    # Ensure the referenced application row exists. Some test suites
    # share a DB session, so we cannot assume the fixture's seed app
    # is the only one in play.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO applications (id, status) VALUES (?, ?)",
            (application_id, "approved"),
        )
        conn.commit()
    except Exception:
        pass
    conn.execute(
        "INSERT INTO monitoring_alerts "
        "(application_id, client_name, alert_type, severity, status, summary, "
        " source_reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            application_id,
            client_name,
            alert_type,
            severity,
            status,
            "test alert summary",
            f"test-alert:{uuid.uuid4().hex}",
        ),
    )
    conn.commit()
    return conn.execute(
        "SELECT id AS id FROM monitoring_alerts "
        "WHERE client_name = ? AND application_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (client_name, application_id),
    ).fetchone()["id"]


def _link_screening_owner(conn, alert_id, application_id):
    case_identifier = f"screening-case-{alert_id}"
    conn.execute(
        "UPDATE monitoring_alerts "
        "SET provider = 'complyadvantage', case_identifier = ? WHERE id = ?",
        (case_identifier, alert_id),
    )
    conn.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier,
             evidence_type, evidence_hash)
        VALUES (?, ?, 'complyadvantage', ?, 'screening_case', ?)
        """,
        (
            alert_id,
            application_id,
            case_identifier,
            f"route-screening-owner-{alert_id}",
        ),
    )
    conn.commit()


def _link_document_owner(conn, alert_id, application_id):
    suffix = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, trigger_key, trigger_label, requirement_key,
             requirement_label, monitoring_alert_id, status)
        VALUES (?, ?, 'Monitoring document signal', ?,
                'Refresh regulated document', ?, 'under_review')
        """,
        (
            application_id,
            f"route-trigger-{suffix}",
            f"route-requirement-{suffix}",
            alert_id,
        ),
    )
    conn.commit()


USER = {"sub": "officer-1", "name": "Test Officer", "role": "co"}


def _alert(conn, alert_id):
    return conn.execute(
        "SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)
    ).fetchone()


def _review(conn, review_id):
    return conn.execute(
        "SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)
    ).fetchone()


def _edd(conn, edd_id):
    return conn.execute(
        "SELECT * FROM edd_cases WHERE id = ?", (edd_id,)
    ).fetchone()


def _approved_review_request(
    conn,
    alert_id,
    *,
    approver="sco-9",
    rationale="Independent senior review.",
):
    source_status = _alert(conn, alert_id)["status"]
    conn.execute(
        """
        INSERT INTO monitoring_alert_review_requests
            (alert_id, tier, requested_outcome, dismissal_reason, rationale,
             evidence_ref, transition_evidence, source_alert_status, state,
             initiated_by, approved_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            2,
            "dismiss",
            "false_positive",
            rationale,
            "registry extract",
            "{}",
            source_status,
            "approved",
            "officer-1",
            approver,
        ),
    )
    return conn.execute(
        "SELECT id FROM monitoring_alert_review_requests "
        "WHERE alert_id = ? ORDER BY id DESC LIMIT 1",
        (alert_id,),
    ).fetchone()["id"]


# ===================================================================
# triage / assign
# ===================================================================
class TestTriageAlert:
    def test_triage_sets_timestamp_and_status(self, routing_db, audit_sink):
        from monitoring_routing import triage_alert
        alert_id = _insert_alert(routing_db)
        result = triage_alert(routing_db, alert_id,
                              user=USER, audit_writer=audit_sink)

        assert result["status"] == "triaged"
        assert result["state_machine_version"] == "monitoring_alert_state_machine_v1"
        row = _alert(routing_db, alert_id)
        assert row["triaged_at"] is not None
        assert row["status"] == "triaged"
        assert any(e["action"] == "monitoring.alert.triaged"
                   for e in audit_sink.events)
        canonical = routing_db.execute(
            "SELECT action FROM audit_log WHERE target = ? ORDER BY id DESC LIMIT 1",
            (f"monitoring_alert:{alert_id}",),
        ).fetchone()
        assert canonical["action"] == "monitoring.alert.status_transition"

        repeated = triage_alert(
            routing_db,
            alert_id,
            user=USER,
            audit_writer=audit_sink,
        )
        assert repeated["changed"] is False
        assert repeated["status"] == "triaged"
        assert repeated["state_machine_version"] == (
            "monitoring_alert_state_machine_v1"
        )

    def test_triage_unknown_alert_raises(self, routing_db, audit_sink):
        from monitoring_routing import triage_alert, AlertNotFound
        with pytest.raises(AlertNotFound):
            triage_alert(routing_db, 9999,
                         user=USER, audit_writer=audit_sink)


class TestAssignAlert:
    def test_assign_sets_status_and_timestamp(self, routing_db, audit_sink):
        from monitoring_routing import assign_alert
        alert_id = _insert_alert(routing_db)
        result = assign_alert(routing_db, alert_id,
                              user=USER, audit_writer=audit_sink)
        assert result["status"] == "assigned"
        assert result["state_machine_version"] == "monitoring_alert_state_machine_v1"
        row = _alert(routing_db, alert_id)
        assert row["assigned_at"] is not None
        assert row["status"] == "assigned"
        assert any(e["action"] == "monitoring.alert.assigned"
                   for e in audit_sink.events)

    def test_co_cannot_assign_another_officer_through_routing_helper(
        self, routing_db, audit_sink
    ):
        import monitoring_alert_state_machine as sm
        from monitoring_routing import assign_alert

        alert_id = _insert_alert(routing_db)
        with pytest.raises(sm.InsufficientRole, match="only self-assign"):
            assign_alert(
                routing_db,
                alert_id,
                user=USER,
                assignee_id="sco-9",
                audit_writer=audit_sink,
            )

        assert _alert(routing_db, alert_id)["status"] == "open"
        assert audit_sink.events == []

    def test_senior_can_assign_another_active_officer_through_routing_helper(
        self, routing_db, audit_sink
    ):
        from monitoring_routing import assign_alert

        alert_id = _insert_alert(routing_db)
        result = assign_alert(
            routing_db,
            alert_id,
            user={"sub": "sco-9", "name": "Senior Officer", "role": "sco"},
            assignee_id="officer-1",
            audit_writer=audit_sink,
        )

        assert result["status"] == "assigned"
        assert result["owner_id"] == "officer-1"
        assert _alert(routing_db, alert_id)["reviewed_by"] == "officer-1"

    def test_cannot_assign_dismissed_alert(self, routing_db, audit_sink):
        from monitoring_routing import (
            assign_alert, dismiss_alert, AlertAlreadyTerminal,
        )
        alert_id = _insert_alert(routing_db)
        dismiss_alert(routing_db, alert_id,
                      dismissal_reason="false_positive",
                      user=USER, audit_writer=audit_sink)
        with pytest.raises(AlertAlreadyTerminal):
            assign_alert(routing_db, alert_id,
                         user=USER, audit_writer=audit_sink)


# ===================================================================
# dismiss
# ===================================================================
class TestDismissAlert:
    def test_dismiss_with_structured_reason(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert
        alert_id = _insert_alert(routing_db)
        result = dismiss_alert(
            routing_db, alert_id,
            dismissal_reason="false_positive",
            dismissal_notes="confirmed not a match",
            user=USER, audit_writer=audit_sink,
        )
        assert result["status"] == "dismissed"
        row = _alert(routing_db, alert_id)
        assert row["status"] == "dismissed"
        assert row["resolved_at"] is not None
        assert row["reviewed_by"] == "officer-1"
        notes = json.loads(row["officer_notes"])
        assert notes["dismissal_reason"] == "false_positive"
        assert notes["dismissal_notes"] == "confirmed not a match"
        assert notes["dismissed_by"] == "officer-1"
        # routing audit event present with structured reason
        routing_events = [e for e in audit_sink.events
                          if e["action"] == "monitoring.alert.dismissed"]
        assert routing_events
        detail = json.loads(routing_events[0]["detail"])
        assert detail["dismissal_reason"] == "false_positive"

    def test_invalid_reason_rejected(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, InvalidDismissalReason
        alert_id = _insert_alert(routing_db)
        with pytest.raises(InvalidDismissalReason):
            dismiss_alert(routing_db, alert_id,
                          dismissal_reason="because",
                          user=USER, audit_writer=audit_sink)
        # alert remains untouched
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open"
        assert row["resolved_at"] is None

    def test_cannot_double_dismiss(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, AlertAlreadyTerminal
        alert_id = _insert_alert(routing_db)
        dismiss_alert(routing_db, alert_id,
                      dismissal_reason="duplicate",
                      user=USER, audit_writer=audit_sink)
        with pytest.raises(AlertAlreadyTerminal):
            dismiss_alert(routing_db, alert_id,
                          dismissal_reason="false_positive",
                          user=USER, audit_writer=audit_sink)


# ===================================================================
# route to periodic review
# ===================================================================
class TestRouteToPeriodicReview:
    def test_creates_review_and_links(self, routing_db, audit_sink):
        from monitoring_routing import route_alert_to_periodic_review
        alert_id = _insert_alert(routing_db)

        result = route_alert_to_periodic_review(
            routing_db, alert_id,
            review_reason="risk drift on monitoring",
            user=USER, audit_writer=audit_sink,
        )
        assert result["created"] is True
        assert result["reused"] is False
        review_id = result["periodic_review_id"]
        review = _review(routing_db, review_id)
        assert review is not None
        assert review["trigger_source"] == "monitoring_alert"
        assert review["linked_monitoring_alert_id"] == alert_id
        assert review["application_id"] == "test-app-100"

        alert = _alert(routing_db, alert_id)
        assert alert["linked_periodic_review_id"] == review_id
        assert alert["status"] == "routed_to_review"
        assert alert["officer_action"] == "route_to_periodic_review"

        actions = [e["action"] for e in audit_sink.events]
        assert "monitoring.alert.routed_to_review" in actions
        assert "lifecycle.link.alert_to_review.created" in actions
        assert "lifecycle.review.trigger_set" in actions

    def test_repeat_route_reuses_review_no_duplicate(self, routing_db,
                                                    audit_sink):
        from monitoring_routing import route_alert_to_periodic_review
        alert_id = _insert_alert(routing_db)

        baseline = routing_db.execute(
            "SELECT COUNT(*) AS c FROM periodic_reviews"
        ).fetchone()["c"]

        first = route_alert_to_periodic_review(
            routing_db, alert_id, user=USER, audit_writer=audit_sink,
        )
        second = route_alert_to_periodic_review(
            routing_db, alert_id, user=USER, audit_writer=audit_sink,
        )

        assert first["periodic_review_id"] == second["periodic_review_id"]
        assert second["created"] is False
        assert second["reused"] is True
        assert second["state_machine_version"] == "monitoring_alert_state_machine_v1"

        # Exactly ONE NEW periodic_reviews row was inserted by both calls
        # combined. Scope by the alert's reverse pointer so this works
        # regardless of any DB rows left by earlier tests.
        rows = routing_db.execute(
            "SELECT id FROM periodic_reviews "
            "WHERE linked_monitoring_alert_id = ?",
            (alert_id,),
        ).fetchall()
        assert len(rows) == 1
        after = routing_db.execute(
            "SELECT COUNT(*) AS c FROM periodic_reviews"
        ).fetchone()["c"]
        assert after - baseline == 1

    def test_dismissed_alert_cannot_route_to_review(self, routing_db,
                                                   audit_sink):
        from monitoring_routing import (
            route_alert_to_periodic_review, dismiss_alert,
            AlertAlreadyTerminal,
        )
        alert_id = _insert_alert(routing_db)
        dismiss_alert(routing_db, alert_id,
                      dismissal_reason="duplicate",
                      user=USER, audit_writer=audit_sink)
        with pytest.raises(AlertAlreadyTerminal):
            route_alert_to_periodic_review(
                routing_db, alert_id,
                user=USER, audit_writer=audit_sink,
            )

    def test_document_owned_alert_transfers_to_periodic_review_owner(
        self,
        routing_db,
        audit_sink,
    ):
        import monitoring_alert_state_machine as sm
        from monitoring_routing import route_alert_to_periodic_review

        application_id = f"test-app-doc-review-{uuid.uuid4().hex[:8]}"
        alert_id = _insert_alert(
            routing_db,
            application_id=application_id,
            alert_type="document_expired",
        )
        _link_document_owner(routing_db, alert_id, application_id)

        result = route_alert_to_periodic_review(
            routing_db,
            alert_id,
            review_reason="Document signal requires downstream review.",
            user=USER,
            audit_writer=audit_sink,
        )
        alert = dict(_alert(routing_db, alert_id))

        assert result["status"] == "routed_to_review"
        assert sm.alert_owner(routing_db, alert) == "periodic_review"
        assert routing_db.execute(
            "SELECT COUNT(*) AS count FROM application_enhanced_requirements "
            "WHERE monitoring_alert_id = ?",
            (alert_id,),
        ).fetchone()["count"] == 1


# ===================================================================
# route to EDD
# ===================================================================
class TestRouteToEDD:
    def test_creates_edd_and_links(self, routing_db, audit_sink):
        from monitoring_routing import route_alert_to_edd
        app = f"test-app-edd-create-{uuid.uuid4().hex[:8]}"
        alert_id = _insert_alert(routing_db, application_id=app)
        result = route_alert_to_edd(
            routing_db, alert_id,
            trigger_notes="elevated risk on monitoring alert",
            user=USER, audit_writer=audit_sink,
        )
        assert result["created"] is True
        edd_id = result["edd_case_id"]

        edd = _edd(routing_db, edd_id)
        assert edd is not None
        assert edd["trigger_source"] == "monitoring_alert"
        assert edd["origin_context"] == "monitoring_alert"
        assert edd["linked_monitoring_alert_id"] == alert_id
        assert edd["application_id"] == app
        assert edd["stage"] == "triggered"

        alert = _alert(routing_db, alert_id)
        assert alert["linked_edd_case_id"] == edd_id
        assert alert["status"] == "routed_to_edd"
        assert alert["officer_action"] == "route_to_edd"

        actions = [e["action"] for e in audit_sink.events]
        assert "monitoring.alert.routed_to_edd" in actions
        assert "lifecycle.link.alert_to_edd.created" in actions
        assert "lifecycle.edd.origin_set" in actions

    def test_screening_owned_alert_transfers_to_edd_owner(
        self,
        routing_db,
        audit_sink,
    ):
        import monitoring_alert_state_machine as sm
        from monitoring_routing import route_alert_to_edd

        application_id = f"test-app-screen-edd-{uuid.uuid4().hex[:8]}"
        alert_id = _insert_alert(
            routing_db,
            application_id=application_id,
            alert_type="sanctions_change",
        )
        _link_screening_owner(routing_db, alert_id, application_id)

        result = route_alert_to_edd(
            routing_db,
            alert_id,
            trigger_notes="Screening signal requires EDD ownership.",
            user=USER,
            audit_writer=audit_sink,
        )
        alert = dict(_alert(routing_db, alert_id))

        assert result["status"] == "routed_to_edd"
        assert sm.alert_owner(routing_db, alert) == "edd"
        assert routing_db.execute(
            "SELECT COUNT(*) AS count FROM monitoring_alert_evidence "
            "WHERE monitoring_alert_id = ?",
            (alert_id,),
        ).fetchone()["count"] == 1

    def test_route_with_two_source_owners_fails_and_rolls_back(
        self,
        routing_db,
        audit_sink,
    ):
        import monitoring_alert_state_machine as sm
        from monitoring_routing import route_alert_to_edd

        application_id = f"test-app-ambiguous-edd-{uuid.uuid4().hex[:8]}"
        alert_id = _insert_alert(
            routing_db,
            application_id=application_id,
            alert_type="sanctions_change",
        )
        _link_screening_owner(routing_db, alert_id, application_id)
        _link_document_owner(routing_db, alert_id, application_id)
        before_edd = routing_db.execute(
            "SELECT COUNT(*) AS count FROM edd_cases"
        ).fetchone()["count"]

        with pytest.raises(sm.AmbiguousAlertOwner):
            route_alert_to_edd(
                routing_db,
                alert_id,
                user=USER,
                audit_writer=audit_sink,
            )

        alert = _alert(routing_db, alert_id)
        assert alert["status"] == "open"
        assert alert["linked_edd_case_id"] is None
        assert routing_db.execute(
            "SELECT COUNT(*) AS count FROM edd_cases"
        ).fetchone()["count"] == before_edd

    def test_repeat_route_reuses_linked_edd(self, routing_db, audit_sink):
        from monitoring_routing import route_alert_to_edd
        app = "test-app-edd-repeat"
        alert_id = _insert_alert(routing_db, application_id=app)
        first = route_alert_to_edd(routing_db, alert_id,
                                   user=USER, audit_writer=audit_sink)
        second = route_alert_to_edd(routing_db, alert_id,
                                    user=USER, audit_writer=audit_sink)

        assert first["edd_case_id"] == second["edd_case_id"]
        assert second["created"] is False
        assert second["reused"] is True
        assert second["state_machine_version"] == "monitoring_alert_state_machine_v1"

        active_count = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases "
            "WHERE application_id = ? AND stage NOT IN "
            "('edd_approved','edd_rejected')",
            (app,),
        ).fetchone()["c"]
        assert active_count == 1

    def test_route_does_not_overwrite_active_edd_owned_by_another_alert(
        self, routing_db, audit_sink,
    ):
        """Two alerts may not share a one-to-one EDD reverse pointer."""
        from monitoring_routing import route_alert_to_edd
        app = "test-app-edd-shared"
        alert_a = _insert_alert(routing_db, application_id=app,
                                client_name="Client A")
        alert_b = _insert_alert(routing_db, application_id=app,
                                client_name="Client B")

        ra = route_alert_to_edd(routing_db, alert_a,
                                user=USER, audit_writer=audit_sink)
        rb = route_alert_to_edd(routing_db, alert_b,
                                user=USER, audit_writer=audit_sink)

        assert ra["edd_case_id"] != rb["edd_case_id"]
        assert rb["reused"] is False
        assert rb["created"] is True

        active_count = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases "
            "WHERE application_id = ? AND stage NOT IN "
            "('edd_approved','edd_rejected')",
            (app,),
        ).fetchone()["c"]
        assert active_count == 2
        assert _edd(
            routing_db,
            ra["edd_case_id"],
        )["linked_monitoring_alert_id"] == alert_a
        assert _edd(
            routing_db,
            rb["edd_case_id"],
        )["linked_monitoring_alert_id"] == alert_b

    @pytest.mark.parametrize(
        ("route_name", "table_name", "alert_link_column"),
        (
            (
                "review",
                "periodic_reviews",
                "linked_periodic_review_id",
            ),
            ("edd", "edd_cases", "linked_edd_case_id"),
        ),
    )
    def test_repeat_route_fails_closed_when_reverse_link_is_corrupted(
        self,
        routing_db,
        audit_sink,
        route_name,
        table_name,
        alert_link_column,
    ):
        from monitoring_routing import (
            route_alert_to_edd,
            route_alert_to_periodic_review,
        )
        import monitoring_alert_state_machine as sm

        alert_id = _insert_alert(
            routing_db,
            application_id=f"test-app-corrupt-{route_name}",
        )
        route = (
            route_alert_to_periodic_review
            if route_name == "review"
            else route_alert_to_edd
        )
        first = route(
            routing_db,
            alert_id,
            user=USER,
            audit_writer=audit_sink,
        )
        linked_id = _alert(routing_db, alert_id)[alert_link_column]
        assert linked_id in {
            first.get("periodic_review_id"),
            first.get("edd_case_id"),
        }
        routing_db.execute(
            f"UPDATE {table_name} SET linked_monitoring_alert_id = 999999 "
            "WHERE id = ?",
            (linked_id,),
        )
        routing_db.commit()

        with pytest.raises(sm.EvidenceLinkMismatch, match="point back"):
            route(
                routing_db,
                alert_id,
                user=USER,
                audit_writer=audit_sink,
            )

    def test_route_creates_new_edd_when_previous_terminal(
        self, routing_db, audit_sink,
    ):
        from monitoring_routing import route_alert_to_edd
        app = "test-app-edd-terminal"
        alert_a = _insert_alert(routing_db, application_id=app,
                                client_name="Client A")
        first = route_alert_to_edd(routing_db, alert_a,
                                   user=USER, audit_writer=audit_sink)
        # Close the first EDD
        routing_db.execute(
            "UPDATE edd_cases SET stage = 'edd_approved' WHERE id = ?",
            (first["edd_case_id"],),
        )
        routing_db.commit()

        alert_b = _insert_alert(routing_db, application_id=app,
                                client_name="Client B")
        second = route_alert_to_edd(routing_db, alert_b,
                                    user=USER, audit_writer=audit_sink)
        assert second["edd_case_id"] != first["edd_case_id"]
        assert second["created"] is True

    def test_dismissed_alert_cannot_route_to_edd(self, routing_db,
                                                 audit_sink):
        from monitoring_routing import (
            route_alert_to_edd, dismiss_alert, AlertAlreadyTerminal,
        )
        alert_id = _insert_alert(routing_db,
                                 application_id="test-app-edd-dismissed")
        dismiss_alert(routing_db, alert_id,
                      dismissal_reason="false_positive",
                      user=USER, audit_writer=audit_sink)
        with pytest.raises(AlertAlreadyTerminal):
            route_alert_to_edd(routing_db, alert_id,
                               user=USER, audit_writer=audit_sink)

    def test_route_without_application_id_rejected(self, routing_db,
                                                   audit_sink):
        from monitoring_routing import route_alert_to_edd, MonitoringRoutingError
        # insert an alert with NULL application_id
        routing_db.execute(
            "INSERT INTO monitoring_alerts "
            "(application_id, client_name, alert_type, severity, status) "
            "VALUES (NULL, 'orphan', 'adverse_media', 'medium', 'open')"
        )
        routing_db.commit()
        alert_id = routing_db.execute(
            "SELECT id FROM monitoring_alerts "
            "WHERE client_name = 'orphan'"
        ).fetchone()["id"]
        with pytest.raises(MonitoringRoutingError):
            route_alert_to_edd(routing_db, alert_id,
                               user=USER, audit_writer=audit_sink)


# ===================================================================
# audit-writer enforcement (delegated to lifecycle_linkage)
# ===================================================================
class TestAuditWriterEnforcement:
    def test_dismiss_requires_audit_writer(self, routing_db):
        from monitoring_routing import dismiss_alert
        from lifecycle_linkage import MissingAuditWriter
        alert_id = _insert_alert(routing_db)
        with pytest.raises(MissingAuditWriter):
            dismiss_alert(routing_db, alert_id,
                          dismissal_reason="false_positive",
                          user=USER, audit_writer=None)
        # alert still untouched
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open"
        assert row["resolved_at"] is None

    def test_route_to_edd_requires_audit_writer(self, routing_db):
        from monitoring_routing import route_alert_to_edd
        from lifecycle_linkage import MissingAuditWriter
        alert_id = _insert_alert(routing_db)
        baseline = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases"
        ).fetchone()["c"]
        with pytest.raises(MissingAuditWriter):
            route_alert_to_edd(routing_db, alert_id,
                               user=USER, audit_writer=None)
        # no edd_cases row created by this call
        after = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases"
        ).fetchone()["c"]
        assert after == baseline


# ===================================================================
# RDI-007 — atomicity: status/linkage/downstream + audit are one txn
# ===================================================================
def _failing_writer(fail_on):
    """Audit writer that records actions and raises on a chosen action, to
    prove a failed audit rolls the whole monitoring action back (RDI-007)."""
    events = []

    def writer(user, action, target, detail, db=None,
               before_state=None, after_state=None, commit=True):
        events.append(action)
        if action == fail_on:
            raise RuntimeError("audit store down")

    writer.events = events
    return writer


class TestRdi007Atomicity:
    def test_audit_joins_transaction_not_committed_early(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert
        alert_id = _insert_alert(routing_db)  # severity="medium"
        dismiss_alert(
            routing_db, alert_id,
            dismissal_reason="false_positive",
            dismissal_notes="fp",
            user=USER, audit_writer=audit_sink,
        )
        # Every audit row emitted during the action joined the caller's open
        # transaction (commit=False) rather than being flushed early.
        acted = [e for e in audit_sink.events
                 if e["action"] in ("monitoring.alert.dismissed",
                                     "lifecycle.alert.resolved")]
        assert acted, "expected routing + lifecycle audit events"
        assert all(e["commit"] is False for e in acted), (
            "RDI-007: routing/lifecycle audit must join the transaction "
            "(commit=False), not commit independently"
        )

    def test_dismiss_rolls_back_when_routing_audit_fails(self, routing_db):
        from monitoring_routing import dismiss_alert
        alert_id = _insert_alert(routing_db)
        writer = _failing_writer("monitoring.alert.dismissed")
        with pytest.raises(RuntimeError, match="audit store down"):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="fp",
                user=USER, audit_writer=writer,
            )
        # Nothing persisted: the resolved_at + status changes rolled back.
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open", (
            "RDI-007: a dismissal whose final routing audit failed must NOT "
            "leave the alert dismissed"
        )
        assert row["resolved_at"] is None

    def test_route_to_edd_rolls_back_when_routing_audit_fails(self, routing_db):
        from monitoring_routing import route_alert_to_edd
        alert_id = _insert_alert(routing_db)
        baseline = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases"
        ).fetchone()["c"]
        writer = _failing_writer("monitoring.alert.routed_to_edd")
        with pytest.raises(RuntimeError, match="audit store down"):
            route_alert_to_edd(
                routing_db, alert_id,
                trigger_notes="n",
                user=USER, audit_writer=writer,
            )
        # The downstream EDD creation and the status transition rolled back.
        after = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases"
        ).fetchone()["c"]
        assert after == baseline, (
            "RDI-007: a route-to-EDD whose routing audit failed must not leave "
            "a dangling EDD case"
        )
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open"
        assert row["linked_edd_case_id"] is None


# ===================================================================
# RDI-008 — CRITICAL alerts cannot be dismissed via the ordinary path
# ===================================================================
_SENIOR = {"sub": "sco-9", "name": "Senior Officer", "role": "sco"}
_VALID_CLEARANCE = {"approver": _SENIOR, "evidence_ref": "SAR-ref-123",
                    "via": "senior_clear"}


class TestRdi008CriticalDismissalGate:
    def test_critical_cannot_dismiss_without_clearance(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, CriticalAlertDismissalBlocked
        alert_id = _insert_alert(routing_db, severity="critical")
        with pytest.raises(CriticalAlertDismissalBlocked):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="looks fine",
                user=USER, audit_writer=audit_sink,
            )
        # Blocked BEFORE any mutation.
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open"
        assert row["resolved_at"] is None

    def test_critical_requires_documented_notes(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, CriticalAlertDismissalBlocked
        alert_id = _insert_alert(routing_db, severity="critical")
        with pytest.raises(CriticalAlertDismissalBlocked):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="",  # missing documented justification
                user=USER, audit_writer=audit_sink,
                critical_clearance=_VALID_CLEARANCE,
            )

    def test_critical_clearance_requires_senior_role(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, CriticalAlertDismissalBlocked
        alert_id = _insert_alert(routing_db, severity="critical")
        junior_clearance = {"approver": {"sub": "co-1", "role": "co"},
                            "evidence_ref": "x"}
        with pytest.raises(CriticalAlertDismissalBlocked):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="fp",
                user=USER, audit_writer=audit_sink,
                critical_clearance=junior_clearance,
            )

    def test_critical_clearance_requires_evidence_or_marker(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, CriticalAlertDismissalBlocked
        alert_id = _insert_alert(routing_db, severity="critical")
        # Senior approver but no evidence_ref and no approved-review marker.
        with pytest.raises(CriticalAlertDismissalBlocked):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="fp",
                user=USER, audit_writer=audit_sink,
                critical_clearance={"approver": _SENIOR},
            )

    def test_critical_dismissed_with_valid_clearance(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert
        alert_id = _insert_alert(routing_db, severity="critical")
        review_request_id = _approved_review_request(
            routing_db,
            alert_id,
            rationale="senior-reviewed false positive",
        )
        result = dismiss_alert(
            routing_db, alert_id,
            dismissal_reason="false_positive",
            dismissal_notes="senior-reviewed false positive",
            user=_SENIOR, audit_writer=audit_sink,
            critical_clearance=_VALID_CLEARANCE,
            review_request_id=review_request_id,
        )
        assert result["status"] == "dismissed"
        row = _alert(routing_db, alert_id)
        assert row["status"] == "dismissed"
        assert row["resolved_at"] is not None
        detail = json.loads([e for e in audit_sink.events
                             if e["action"] == "monitoring.alert.dismissed"][0]["detail"])
        assert detail["critical_gate_applied"] is True

    def test_critical_dismissed_via_approved_review_with_evidence(self, routing_db, audit_sink):
        # Codex round-2 hardening: even an approved second review must carry
        # the stored request evidence (the server threads it after the
        # approval-time recheck) — the marker alone is no longer sufficient.
        from monitoring_routing import dismiss_alert
        alert_id = _insert_alert(routing_db, severity="critical")
        review_request_id = _approved_review_request(
            routing_db,
            alert_id,
            rationale="approved after second review",
        )
        result = dismiss_alert(
            routing_db, alert_id,
            dismissal_reason="false_positive",
            dismissal_notes="approved after second review",
            user=_SENIOR, audit_writer=audit_sink,
            critical_clearance={"approver": _SENIOR, "via": "approved_review",
                                "evidence_ref": "registry extract"},
            review_request_id=review_request_id,
        )
        assert result["status"] == "dismissed"

    def test_non_critical_dismissal_unaffected(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert
        alert_id = _insert_alert(routing_db, severity="medium")
        result = dismiss_alert(
            routing_db, alert_id,
            dismissal_reason="false_positive",
            dismissal_notes="fp",
            user=USER, audit_writer=audit_sink,
        )
        assert result["status"] == "dismissed"

    # ── Codex-validation follow-up (2026-07-25): a bare senior_clear marker
    #    WITHOUT evidence must no longer pass the service gate ──
    def test_bare_senior_clear_marker_without_evidence_rejected(self, routing_db, audit_sink):
        from monitoring_routing import dismiss_alert, CriticalAlertDismissalBlocked
        alert_id = _insert_alert(routing_db, severity="critical")
        no_evidence = {"approver": _SENIOR, "via": "senior_clear"}  # no evidence_ref
        with pytest.raises(CriticalAlertDismissalBlocked):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="senior says fine",
                user=_SENIOR, audit_writer=audit_sink,
                critical_clearance=no_evidence,
            )
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open"

    def test_approved_review_marker_without_evidence_rejected(self, routing_db, audit_sink):
        # Codex round-2 hardening: the approved_review marker WITHOUT evidence
        # no longer passes — this closes the residual race where a severity
        # escalation lands between the approval-time evidence recheck and this
        # gate's fresh alert fetch.
        from monitoring_routing import dismiss_alert, CriticalAlertDismissalBlocked
        alert_id = _insert_alert(routing_db, severity="critical")
        with pytest.raises(CriticalAlertDismissalBlocked):
            dismiss_alert(
                routing_db, alert_id,
                dismissal_reason="false_positive",
                dismissal_notes="approved via second review",
                user=_SENIOR, audit_writer=audit_sink,
                critical_clearance={"approver": _SENIOR, "via": "approved_review"},
            )
        row = _alert(routing_db, alert_id)
        assert row["status"] == "open"


@pytest.fixture()
def monitoring_postgres_schema(monkeypatch):
    """Initialize the production engine in an isolated PostgreSQL database."""
    base_dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not base_dsn:
        pytest.fail(
            "TEST_POSTGRES_DSN is required for the protected PostgreSQL "
            "schema contract",
            pytrace=False,
        )

    from contextlib import suppress
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import make_dsn

    database_name = f"monitoring_owner_{uuid.uuid4().hex[:12]}"
    database_dsn = make_dsn(base_dsn, dbname=database_name)
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    db_module = None
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(database_name)
                )
            )
        created = True
        monkeypatch.setenv("DATABASE_URL", database_dsn)
        monkeypatch.setenv("ENVIRONMENT", "testing")

        import db as loaded_db

        db_module = loaded_db
        db_module.close_pg_pool()
        monkeypatch.setattr(db_module, "DATABASE_URL", database_dsn)
        monkeypatch.setattr(db_module, "USE_POSTGRESQL", True)
        db_module.init_db()
        connection = db_module.get_db()
        try:
            yield connection
        finally:
            connection.close()
    finally:
        if db_module is not None:
            with suppress(Exception):
                db_module.close_pg_pool()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                    ).format(sql.Identifier(database_name))
                )
        admin.close()


class TestProtectedDomainOwnershipBoundary:
    """Monitoring may orchestrate canonical owners, never become one.

    These are behavioral architecture guards for the protected baseline.
    Routing is allowed to create/link the canonical periodic-review or EDD
    record. It must not create KYC, Screening Review, Change Management, or
    memo/AI judgment state, and the monitoring table must not acquire columns
    that duplicate those owners.
    """

    _NON_OWNER_TABLES = (
        "documents",
        "screening_reviews",
        "change_requests",
        "compliance_memos",
    )
    _MONITORING_SCHEMA_ALLOWLIST = {
        "monitoring_agent_status": {
            "id", "agent_name", "agent_type", "last_run", "next_run",
            "run_frequency", "clients_monitored", "alerts_generated", "status",
        },
        "monitoring_alert_escalations": {
            "id", "alert_id", "reason", "escalated_by", "escalated_by_role",
            "escalated_at", "prior_status", "new_status", "sla_state",
            "days_overdue", "sla_due_at", "sla_days",
            "alert_severity_at_escalation",
        },
        "monitoring_alert_evidence": {
            "id", "monitoring_alert_id", "application_id", "provider",
            "case_identifier", "alert_identifier", "match_identifier",
            "risk_identifier", "profile_identifier", "evidence_type",
            "matched_subject_name", "relationship_to_client",
            "match_category", "risk_indicator", "match_confidence",
            "source_title", "source_name", "source_url",
            "source_url_available", "source_url_unavailable_reason",
            "publication_date", "snippet", "provider_case_url",
            "evidence_json", "raw_provider_reference", "evidence_status",
            "evidence_hash", "fetched_at", "created_at", "updated_at",
        },
        "monitoring_alert_followups": {
            "id", "alert_id", "action", "note", "due_at", "created_by",
            "created_at", "resolved_at", "resolved_by",
        },
        "monitoring_alert_review_requests": {
            "id", "alert_id", "tier", "requested_outcome",
            "dismissal_reason", "rationale", "evidence_ref", "state",
            "initiated_by", "initiated_at", "approved_by", "approved_at",
            "approval_note", "rejection_reason", "second_review_bypassed",
            "sampled_for_qa", "transition_evidence", "source_alert_status",
        },
        "monitoring_alerts": {
            "id", "application_id", "provider", "case_identifier",
            "discovered_via", "discovered_at", "backfill_run_id",
            "client_name", "alert_type", "severity", "detected_by", "summary",
            "source_reference", "ai_recommendation", "status",
            "officer_action", "officer_notes", "created_at", "reviewed_at",
            "reviewed_by", "linked_periodic_review_id", "linked_edd_case_id",
            "triaged_at", "assigned_at", "resolved_at",
        },
        "screening_monitoring_subscriptions": {
            "id", "client_id", "application_id", "provider", "person_key",
            "customer_identifier", "external_subscription_id", "status",
            "subscribed_at", "last_event_at", "last_webhook_type",
            "monitoring_event_count", "is_authoritative", "source",
            "created_at", "updated_at",
        },
    }

    @staticmethod
    def _counts(connection):
        existing = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        return {
            table: connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            for table in TestProtectedDomainOwnershipBoundary._NON_OWNER_TABLES
            if table in existing
        }

    def test_monitoring_schema_is_an_explicit_signal_orchestration_allowlist(
        self, routing_db
    ):
        tables = {
            row["name"]
            for row in routing_db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name LIKE '%monitoring%'
                """
            ).fetchall()
        }
        assert tables == set(self._MONITORING_SCHEMA_ALLOWLIST)
        for table, expected_columns in self._MONITORING_SCHEMA_ALLOWLIST.items():
            actual_columns = {
                row["name"]
                for row in routing_db.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            assert actual_columns == expected_columns, table

    def test_postgresql_monitoring_schema_matches_the_same_allowlist(
        self, monitoring_postgres_schema
    ):
        tables = {
            row["table_name"]
            for row in monitoring_postgres_schema.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                  AND table_name LIKE ?
                """,
                ("%monitoring%",),
            ).fetchall()
        }
        assert tables == set(self._MONITORING_SCHEMA_ALLOWLIST)
        for table, expected_columns in self._MONITORING_SCHEMA_ALLOWLIST.items():
            actual_columns = {
                row["column_name"]
                for row in monitoring_postgres_schema.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = ?
                    """,
                    (table,),
                ).fetchall()
            }
            assert actual_columns == expected_columns, table

    def test_route_to_edd_only_links_canonical_edd_owner(
        self, routing_db, audit_sink
    ):
        from monitoring_routing import route_alert_to_edd

        alert_id = _insert_alert(
            routing_db,
            application_id="protected-owner-edd",
            client_name="Protected owner EDD",
        )
        before = self._counts(routing_db)
        edd_before = routing_db.execute(
            "SELECT COUNT(*) AS count FROM edd_cases"
        ).fetchone()["count"]

        result = route_alert_to_edd(
            routing_db,
            alert_id,
            trigger_notes="Route signal to canonical EDD owner",
            user=USER,
            audit_writer=audit_sink,
        )

        assert self._counts(routing_db) == before
        assert routing_db.execute(
            "SELECT COUNT(*) AS count FROM edd_cases"
        ).fetchone()["count"] == edd_before + 1
        edd = _edd(routing_db, result["edd_case_id"])
        assert edd["origin_context"] == "monitoring_alert"
        assert edd["linked_monitoring_alert_id"] == alert_id
        assert edd["stage"] == "triggered"
        assert edd["decision"] is None
        assert edd["decision_reason"] is None
        assert edd["decided_by"] is None
        alert = _alert(routing_db, alert_id)
        assert alert["linked_edd_case_id"] == result["edd_case_id"]
        assert alert["status"] == "routed_to_edd"
        assert alert["ai_recommendation"] is None

    def test_route_to_periodic_review_only_links_canonical_review_owner(
        self, routing_db, audit_sink
    ):
        from monitoring_routing import route_alert_to_periodic_review

        alert_id = _insert_alert(
            routing_db,
            application_id="protected-owner-review",
            client_name="Protected owner review",
        )
        before = self._counts(routing_db)
        review_before = routing_db.execute(
            "SELECT COUNT(*) AS count FROM periodic_reviews"
        ).fetchone()["count"]

        result = route_alert_to_periodic_review(
            routing_db,
            alert_id,
            review_reason="Route signal to canonical periodic-review owner",
            user=USER,
            audit_writer=audit_sink,
        )

        assert self._counts(routing_db) == before
        assert routing_db.execute(
            "SELECT COUNT(*) AS count FROM periodic_reviews"
        ).fetchone()["count"] == review_before + 1
        review = _review(routing_db, result["periodic_review_id"])
        assert review["trigger_source"] == "monitoring_alert"
        assert review["linked_monitoring_alert_id"] == alert_id
        assert review["status"] == "pending"
        assert review["decision"] is None
        assert review["outcome"] is None
        alert = _alert(routing_db, alert_id)
        assert alert["linked_periodic_review_id"] == result["periodic_review_id"]
        assert alert["status"] == "routed_to_review"
        assert alert["ai_recommendation"] is None
