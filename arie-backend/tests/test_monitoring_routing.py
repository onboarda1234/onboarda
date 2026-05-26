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
    db_module._DB_PATH = str(tmp_path / "test.db")
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
    conn.commit()

    from migrations.runner import run_all_migrations_with_connection
    run_all_migrations_with_connection(conn)

    yield conn
    conn.close()


@pytest.fixture
def audit_sink():
    events = []

    def writer(user, action, target, detail, db=None,
               before_state=None, after_state=None):
        events.append({
            "user": dict(user) if user else {},
            "action": action,
            "target": target,
            "detail": detail,
            "before_state": before_state,
            "after_state": after_state,
        })

    writer.events = events
    return writer


def _insert_alert(conn, *, application_id="test-app-100",
                  client_name="Client A", status="open",
                  severity="medium"):
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
        "(application_id, client_name, alert_type, severity, status, summary) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (application_id, client_name, "adverse_media", severity, status,
         "test alert summary"),
    )
    conn.commit()
    return conn.execute(
        "SELECT id AS id FROM monitoring_alerts "
        "WHERE client_name = ? AND application_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (client_name, application_id),
    ).fetchone()["id"]


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
        row = _alert(routing_db, alert_id)
        assert row["triaged_at"] is not None
        assert row["status"] == "triaged"
        assert any(e["action"] == "monitoring.alert.triaged"
                   for e in audit_sink.events)
        # PR-01 lifecycle event also recorded
        assert any(e["action"] == "lifecycle.alert.triaged"
                   for e in audit_sink.events)

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
        row = _alert(routing_db, alert_id)
        assert row["assigned_at"] is not None
        assert row["status"] == "assigned"
        assert any(e["action"] == "monitoring.alert.assigned"
                   for e in audit_sink.events)

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


# ===================================================================
# route to EDD
# ===================================================================
class TestRouteToEDD:
    def test_creates_edd_and_links(self, routing_db, audit_sink):
        from monitoring_routing import route_alert_to_edd
        app = "test-app-edd-create"
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

        active_count = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases "
            "WHERE application_id = ? AND stage NOT IN "
            "('edd_approved','edd_rejected')",
            (app,),
        ).fetchone()["c"]
        assert active_count == 1

    def test_route_reuses_existing_active_edd_for_same_application(
        self, routing_db, audit_sink,
    ):
        """Two distinct alerts on same app => only one active EDD."""
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

        assert ra["edd_case_id"] == rb["edd_case_id"]
        assert rb["reused"] is True
        assert rb["created"] is False

        active_count = routing_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases "
            "WHERE application_id = ? AND stage NOT IN "
            "('edd_approved','edd_rejected')",
            (app,),
        ).fetchone()["c"]
        assert active_count == 1

        # The reused link still set linked_monitoring_alert_id on the
        # EDD via set_edd_origin to point at the most-recently-routing
        # alert (the deterministic outcome documented in the routing
        # contract; PR-01 link helpers handle the displacement).
        edd = _edd(routing_db, ra["edd_case_id"])
        assert edd["linked_monitoring_alert_id"] == alert_b

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
