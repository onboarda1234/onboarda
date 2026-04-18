"""
Tests for lifecycle_linkage helpers -- PR-01 foundation.

Verifies:
  * set_edd_origin / set_periodic_review_trigger persist values and
    record structured audit events;
  * link_alert_to_edd / link_alert_to_review produce bidirectional
    soft-links and emit the correct audit events;
  * unlink helpers clear both sides and emit removal audit events;
  * invalid enums are rejected (InvalidEnumValue);
  * missing referenced IDs are rejected (ReferencedRowNotFound);
  * closed / terminal lifecycle rows reject further updates
    (InvalidLifecycleTransition);
  * injected audit_writer receives the structured detail payload;
  * no mutation of unrelated records.
"""

import json
import os
import sys

import pytest

# Make arie-backend importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def lifecycle_db(tmp_path, monkeypatch):
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
    try:
        conn.execute(
            "INSERT INTO applications (id, status) VALUES (?, ?)",
            ("test-app-100", "submitted"),
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


def _insert_edd(conn, client_name="Client A", stage="triggered"):
    conn.execute(
        "INSERT INTO edd_cases (application_id, client_name, stage) "
        "VALUES (?, ?, ?)",
        ("test-app-100", client_name, stage),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM edd_cases WHERE client_name = ? ORDER BY id DESC LIMIT 1",
        (client_name,),
    ).fetchone()[0]


def _insert_alert(conn, client_name="Client A", status="open"):
    conn.execute(
        "INSERT INTO monitoring_alerts "
        "(application_id, client_name, alert_type, severity, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("test-app-100", client_name, "adverse_media", "medium", status),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM monitoring_alerts WHERE client_name = ? ORDER BY id DESC LIMIT 1",
        (client_name,),
    ).fetchone()[0]


def _insert_review(conn, client_name="Client A"):
    conn.execute(
        "INSERT INTO periodic_reviews (application_id, client_name) "
        "VALUES (?, ?)",
        ("test-app-100", client_name),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM periodic_reviews WHERE client_name = ? ORDER BY id DESC LIMIT 1",
        (client_name,),
    ).fetchone()[0]


USER = {"sub": "officer-1", "name": "Test Officer", "role": "compliance_officer"}


# ═══════════════════════════════════════════════════════════════════
# set_edd_origin
# ═══════════════════════════════════════════════════════════════════

class TestSetEddOrigin:
    def test_origin_from_monitoring_alert_persists(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import set_edd_origin
        alert_id = _insert_alert(lifecycle_db)
        edd_id = _insert_edd(lifecycle_db)
        set_edd_origin(
            lifecycle_db, edd_id,
            origin_context="monitoring_alert",
            linked_monitoring_alert_id=alert_id,
            user=USER, audit_writer=audit_sink,
        )
        row = lifecycle_db.execute(
            "SELECT origin_context, linked_monitoring_alert_id "
            "FROM edd_cases WHERE id = ?",
            (edd_id,),
        ).fetchone()
        assert row[0] == "monitoring_alert"
        assert row[1] == alert_id
        actions = [e["action"] for e in audit_sink.events]
        assert "lifecycle.edd.origin_set" in actions
        ev = next(e for e in audit_sink.events if e["action"] == "lifecycle.edd.origin_set")
        assert ev["target"] == f"edd_case:{edd_id}"
        payload = json.loads(ev["detail"])
        assert payload["origin_context"] == "monitoring_alert"
        assert payload["linked_monitoring_alert_id"] == alert_id

    def test_invalid_origin_context_rejected(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import set_edd_origin, InvalidEnumValue
        edd_id = _insert_edd(lifecycle_db)
        with pytest.raises(InvalidEnumValue):
            set_edd_origin(
                lifecycle_db, edd_id,
                origin_context="bogus_source",
                user=USER, audit_writer=audit_sink,
            )
        assert audit_sink.events == []  # nothing should be audited

    def test_monitoring_origin_requires_alert_id(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import set_edd_origin, LifecycleLinkageError
        edd_id = _insert_edd(lifecycle_db)
        with pytest.raises(LifecycleLinkageError):
            set_edd_origin(
                lifecycle_db, edd_id,
                origin_context="monitoring_alert",
                linked_monitoring_alert_id=None,
                user=USER, audit_writer=audit_sink,
            )

    def test_nonexistent_alert_id_rejected(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import set_edd_origin, ReferencedRowNotFound
        edd_id = _insert_edd(lifecycle_db)
        with pytest.raises(ReferencedRowNotFound):
            set_edd_origin(
                lifecycle_db, edd_id,
                origin_context="monitoring_alert",
                linked_monitoring_alert_id=99999,
                user=USER, audit_writer=audit_sink,
            )


# ═══════════════════════════════════════════════════════════════════
# set_periodic_review_trigger
# ═══════════════════════════════════════════════════════════════════

class TestSetPeriodicReviewTrigger:
    def test_schedule_trigger_persists(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import set_periodic_review_trigger
        review_id = _insert_review(lifecycle_db)
        set_periodic_review_trigger(
            lifecycle_db, review_id,
            trigger_source="schedule",
            review_reason="Annual review per risk policy",
            user=USER, audit_writer=audit_sink,
        )
        row = lifecycle_db.execute(
            "SELECT trigger_source, review_reason FROM periodic_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        assert row[0] == "schedule"
        assert row[1] == "Annual review per risk policy"
        assert any(e["action"] == "lifecycle.review.trigger_set"
                   for e in audit_sink.events)

    def test_invalid_trigger_source_rejected(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import set_periodic_review_trigger, InvalidEnumValue
        review_id = _insert_review(lifecycle_db)
        with pytest.raises(InvalidEnumValue):
            set_periodic_review_trigger(
                lifecycle_db, review_id,
                trigger_source="whatever",
                user=USER, audit_writer=audit_sink,
            )

    def test_existing_trigger_type_not_touched(self, lifecycle_db, audit_sink):
        """Setting trigger_source must NOT mutate trigger_type / trigger_reason."""
        from lifecycle_linkage import set_periodic_review_trigger
        review_id = _insert_review(lifecycle_db)
        lifecycle_db.execute(
            "UPDATE periodic_reviews SET trigger_type = ?, trigger_reason = ? "
            "WHERE id = ?",
            ("risk_recomputation", "Upstream risk-config bump", review_id),
        )
        lifecycle_db.commit()
        set_periodic_review_trigger(
            lifecycle_db, review_id,
            trigger_source="schedule",
            user=USER, audit_writer=audit_sink,
        )
        row = lifecycle_db.execute(
            "SELECT trigger_type, trigger_reason, trigger_source "
            "FROM periodic_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        assert row[0] == "risk_recomputation"
        assert row[1] == "Upstream risk-config bump"
        assert row[2] == "schedule"


# ═══════════════════════════════════════════════════════════════════
# link / unlink alert <-> EDD
# ═══════════════════════════════════════════════════════════════════

class TestLinkAlertToEdd:
    def test_bidirectional_link_and_audit(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import link_alert_to_edd
        alert_id = _insert_alert(lifecycle_db)
        edd_id = _insert_edd(lifecycle_db)
        link_alert_to_edd(
            lifecycle_db, alert_id, edd_id,
            user=USER, audit_writer=audit_sink,
        )
        alert_link = lifecycle_db.execute(
            "SELECT linked_edd_case_id FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()[0]
        edd_link = lifecycle_db.execute(
            "SELECT linked_monitoring_alert_id FROM edd_cases WHERE id = ?",
            (edd_id,),
        ).fetchone()[0]
        assert alert_link == edd_id
        assert edd_link == alert_id
        assert any(e["action"] == "lifecycle.link.alert_to_edd.created"
                   for e in audit_sink.events)

    def test_cannot_link_to_terminal_edd(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import link_alert_to_edd, InvalidLifecycleTransition
        alert_id = _insert_alert(lifecycle_db)
        edd_id = _insert_edd(lifecycle_db, client_name="Terminal", stage="edd_approved")
        with pytest.raises(InvalidLifecycleTransition):
            link_alert_to_edd(
                lifecycle_db, alert_id, edd_id,
                user=USER, audit_writer=audit_sink,
            )

    def test_unlink_clears_both_sides_and_audits(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import link_alert_to_edd, unlink_alert_from_edd
        alert_id = _insert_alert(lifecycle_db)
        edd_id = _insert_edd(lifecycle_db)
        link_alert_to_edd(lifecycle_db, alert_id, edd_id,
                          user=USER, audit_writer=audit_sink)
        unlink_alert_from_edd(lifecycle_db, alert_id,
                              user=USER, audit_writer=audit_sink)
        alert_link = lifecycle_db.execute(
            "SELECT linked_edd_case_id FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()[0]
        edd_link = lifecycle_db.execute(
            "SELECT linked_monitoring_alert_id FROM edd_cases WHERE id = ?",
            (edd_id,),
        ).fetchone()[0]
        assert alert_link is None
        assert edd_link is None
        removal_events = [e for e in audit_sink.events
                          if e["action"] == "lifecycle.link.alert_to_edd.removed"]
        assert len(removal_events) == 1
        detail = json.loads(removal_events[0]["detail"])
        assert detail["previous_edd_case_id"] == edd_id


# ═══════════════════════════════════════════════════════════════════
# link / unlink alert <-> review
# ═══════════════════════════════════════════════════════════════════

class TestLinkAlertToReview:
    def test_bidirectional_link(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import link_alert_to_review
        alert_id = _insert_alert(lifecycle_db)
        review_id = _insert_review(lifecycle_db)
        link_alert_to_review(
            lifecycle_db, alert_id, review_id,
            user=USER, audit_writer=audit_sink,
        )
        alert_link = lifecycle_db.execute(
            "SELECT linked_periodic_review_id FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()[0]
        review_link = lifecycle_db.execute(
            "SELECT linked_monitoring_alert_id FROM periodic_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()[0]
        assert alert_link == review_id
        assert review_link == alert_id

    def test_cannot_link_to_closed_review(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import (
            link_alert_to_review, InvalidLifecycleTransition, mark_review_closed,
        )
        alert_id = _insert_alert(lifecycle_db)
        review_id = _insert_review(lifecycle_db)
        mark_review_closed(lifecycle_db, review_id,
                           user=USER, audit_writer=audit_sink)
        with pytest.raises(InvalidLifecycleTransition):
            link_alert_to_review(
                lifecycle_db, alert_id, review_id,
                user=USER, audit_writer=audit_sink,
            )


# ═══════════════════════════════════════════════════════════════════
# lifecycle timestamp helpers
# ═══════════════════════════════════════════════════════════════════

class TestLifecycleTimestamps:
    def test_mark_edd_assigned_sets_assigned_at_and_priority(
        self, lifecycle_db, audit_sink
    ):
        from lifecycle_linkage import mark_edd_assigned
        edd_id = _insert_edd(lifecycle_db)
        mark_edd_assigned(
            lifecycle_db, edd_id,
            priority="high", sla_due_at="2026-05-01T00:00:00+00:00",
            user=USER, audit_writer=audit_sink,
        )
        row = lifecycle_db.execute(
            "SELECT assigned_at, priority, sla_due_at FROM edd_cases WHERE id = ?",
            (edd_id,),
        ).fetchone()
        assert row[0] is not None
        assert row[1] == "high"
        assert row[2] == "2026-05-01T00:00:00+00:00"

    def test_invalid_priority_rejected(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import mark_edd_assigned, InvalidEnumValue
        edd_id = _insert_edd(lifecycle_db)
        with pytest.raises(InvalidEnumValue):
            mark_edd_assigned(
                lifecycle_db, edd_id,
                priority="SUPER_URGENT",
                user=USER, audit_writer=audit_sink,
            )

    def test_mark_edd_escalated_rejects_terminal_stage(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import mark_edd_escalated, InvalidLifecycleTransition
        edd_id = _insert_edd(lifecycle_db, client_name="Terminal2", stage="edd_rejected")
        with pytest.raises(InvalidLifecycleTransition):
            mark_edd_escalated(
                lifecycle_db, edd_id, reason="late escalation attempt",
                user=USER, audit_writer=audit_sink,
            )

    def test_mark_alert_resolved_writes_timestamp(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import mark_alert_resolved
        alert_id = _insert_alert(lifecycle_db)
        mark_alert_resolved(lifecycle_db, alert_id,
                            user=USER, audit_writer=audit_sink)
        row = lifecycle_db.execute(
            "SELECT resolved_at FROM monitoring_alerts WHERE id = ?",
            (alert_id,),
        ).fetchone()
        assert row[0] is not None


# ═══════════════════════════════════════════════════════════════════
# isolation: unrelated rows remain untouched
# ═══════════════════════════════════════════════════════════════════

class TestNoCollateralMutation:
    def test_linking_does_not_touch_other_edd_cases(self, lifecycle_db, audit_sink):
        from lifecycle_linkage import link_alert_to_edd
        alert_id = _insert_alert(lifecycle_db, client_name="Target")
        edd_target_id = _insert_edd(lifecycle_db, client_name="TargetEDD")
        edd_other_id = _insert_edd(lifecycle_db, client_name="OtherEDD")
        link_alert_to_edd(lifecycle_db, alert_id, edd_target_id,
                          user=USER, audit_writer=audit_sink)
        other_row = lifecycle_db.execute(
            "SELECT linked_monitoring_alert_id FROM edd_cases WHERE id = ?",
            (edd_other_id,),
        ).fetchone()
        assert other_row[0] is None

    def test_no_audit_writer_is_tolerated(self, lifecycle_db):
        """Helpers must not crash when audit_writer is None."""
        from lifecycle_linkage import set_edd_origin
        edd_id = _insert_edd(lifecycle_db, client_name="NoAudit")
        set_edd_origin(
            lifecycle_db, edd_id,
            origin_context="manual",
            user=USER, audit_writer=None,
        )
        row = lifecycle_db.execute(
            "SELECT origin_context FROM edd_cases WHERE id = ?",
            (edd_id,),
        ).fetchone()
        assert row[0] == "manual"
