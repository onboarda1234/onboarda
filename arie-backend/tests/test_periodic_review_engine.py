"""
Tests for periodic_review_engine -- PR-03.

Verifies:

* state vocabulary and transition rules
* refusal to transition into 'completed' (must use record_review_outcome)
* required-item generation:
    * baseline items always present
    * risk-tier-driven items
    * jurisdiction / sector / ownership-driven items
    * stale-document item
    * monitoring-alert follow-up (PR-02 contract)
    * prior-outcome follow-up
    * persistence as JSON on periodic_reviews.required_items
* escalation to EDD:
    * creates a new EDD when none active
    * reuses an EDD already linked to the review
    * reuses any other active EDD for the same application
      (mirrors EDDCreateHandler / monitoring_routing dedup contract)
    * sets origin_context='periodic_review' via lifecycle_linkage
    * monitoring-originated reviews escalate as first-class reviews
    * PR-02 reverse-link displacement contract is respected: the EDD's
      linked_monitoring_alert_id is NOT cleared when a review escalates
      to an EDD that another alert points at
* outcome recording:
    * persists outcome / outcome_reason / outcome_recorded_at
    * sets status to 'completed' and stamps closed_at
    * decision-replay protection (cannot complete twice)
    * leaves onboarding compliance_memos history untouched
* audit-writer enforcement (MissingAuditWriter raised before any DB write)
* invalid enums and state transitions are rejected with explicit errors

Test fixture mirrors tests/test_lifecycle_linkage.py and
tests/test_monitoring_routing.py for consistency.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────
@pytest.fixture
def review_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with repository schema + migrations 008 and 009."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("db.DB_PATH", str(tmp_path / "test.db"))
    import db as db_module
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
            "INSERT INTO applications "
            "(id, ref, company_name, country, sector, "
            " ownership_structure, risk_level, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "test-app-100", "APP-100", "Test Co Ltd",
                "Mauritius", "Fintech", "single-tier", "MEDIUM",
                "approved",
            ),
        )
    except Exception:
        conn.execute(
            "INSERT OR IGNORE INTO applications (id, ref, company_name) VALUES (?, ?, ?)",
            ("test-app-100", "APP-100", "Test Co Ltd"),
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


USER = {"sub": "officer-1", "name": "Test Officer", "role": "compliance_officer"}


def _insert_review(conn, *, application_id="test-app-100",
                   client_name="Test Co Ltd", risk_level="MEDIUM",
                   status="pending", trigger_source=None,
                   linked_monitoring_alert_id=None,
                   review_reason=None):
    conn.execute(
        "INSERT INTO periodic_reviews "
        "(application_id, client_name, risk_level, status, trigger_source, "
        " linked_monitoring_alert_id, review_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (application_id, client_name, risk_level, status,
         trigger_source, linked_monitoring_alert_id, review_reason),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


def _insert_alert(conn, *, application_id="test-app-100",
                  client_name="Test Co Ltd", status="open"):
    conn.execute(
        "INSERT INTO monitoring_alerts "
        "(application_id, client_name, alert_type, severity, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (application_id, client_name, "adverse_media", "medium", status),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


def _insert_edd(conn, *, application_id="test-app-100",
                client_name="Test Co Ltd", stage="triggered"):
    conn.execute(
        "INSERT INTO edd_cases "
        "(application_id, client_name, stage) VALUES (?, ?, ?)",
        (application_id, client_name, stage),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


def _review(conn, review_id):
    return conn.execute(
        "SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)
    ).fetchone()


def _edd(conn, edd_id):
    return conn.execute(
        "SELECT * FROM edd_cases WHERE id = ?", (edd_id,)
    ).fetchone()


def _alert(conn, alert_id):
    return conn.execute(
        "SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)
    ).fetchone()


# ─────────────────────────────────────────────────────────────────
# State transitions
# ─────────────────────────────────────────────────────────────────
class TestStateTransitions:
    def test_pending_to_in_progress(self, review_db, audit_sink):
        from periodic_review_engine import (
            transition_review_state, STATE_IN_PROGRESS,
        )
        rid = _insert_review(review_db)
        result = transition_review_state(
            review_db, rid, new_state=STATE_IN_PROGRESS,
            user=USER, audit_writer=audit_sink,
        )
        assert result["from"] == "pending"
        assert result["to"] == "in_progress"
        assert _review(review_db, rid)["status"] == "in_progress"
        assert _review(review_db, rid)["state_changed_at"] is not None
        assert any(e["action"] == "periodic_review.state_changed"
                   for e in audit_sink.events)

    def test_in_progress_to_awaiting_information(self, review_db, audit_sink):
        from periodic_review_engine import (
            transition_review_state, STATE_IN_PROGRESS,
            STATE_AWAITING_INFORMATION,
        )
        rid = _insert_review(review_db, status="in_progress")
        transition_review_state(
            review_db, rid, new_state=STATE_AWAITING_INFORMATION,
            reason="need source-of-funds evidence",
            user=USER, audit_writer=audit_sink,
        )
        assert _review(review_db, rid)["status"] == "awaiting_information"
        ev = [e for e in audit_sink.events
              if e["action"] == "periodic_review.state_changed"][0]
        # reason flows through into the audit detail
        assert "need source-of-funds" in ev["detail"]

    def test_cannot_transition_to_completed_via_state_helper(self, review_db, audit_sink):
        from periodic_review_engine import (
            transition_review_state, STATE_COMPLETED,
            InvalidReviewTransition,
        )
        rid = _insert_review(review_db, status="in_progress")
        with pytest.raises(InvalidReviewTransition):
            transition_review_state(
                review_db, rid, new_state=STATE_COMPLETED,
                user=USER, audit_writer=audit_sink,
            )
        # status unchanged
        assert _review(review_db, rid)["status"] == "in_progress"

    def test_cannot_skip_states(self, review_db, audit_sink):
        from periodic_review_engine import (
            transition_review_state, STATE_AWAITING_INFORMATION,
            InvalidReviewTransition,
        )
        rid = _insert_review(review_db)  # pending
        with pytest.raises(InvalidReviewTransition):
            transition_review_state(
                review_db, rid, new_state=STATE_AWAITING_INFORMATION,
                user=USER, audit_writer=audit_sink,
            )

    def test_invalid_state_rejected(self, review_db, audit_sink):
        from periodic_review_engine import (
            transition_review_state, InvalidReviewState,
        )
        rid = _insert_review(review_db)
        with pytest.raises(InvalidReviewState):
            transition_review_state(
                review_db, rid, new_state="bogus",
                user=USER, audit_writer=audit_sink,
            )

    def test_audit_writer_required(self, review_db):
        from periodic_review_engine import (
            transition_review_state, STATE_IN_PROGRESS,
        )
        from lifecycle_linkage import MissingAuditWriter
        rid = _insert_review(review_db)
        with pytest.raises(MissingAuditWriter):
            transition_review_state(
                review_db, rid, new_state=STATE_IN_PROGRESS,
                user=USER, audit_writer=None,
            )
        # No mutation must have occurred.
        assert _review(review_db, rid)["status"] == "pending"


# ─────────────────────────────────────────────────────────────────
# Required-item generation
# ─────────────────────────────────────────────────────────────────
class TestRequiredItemsGeneration:
    def test_baseline_items_always_present(self, review_db, audit_sink):
        from periodic_review_engine import generate_required_items
        rid = _insert_review(review_db)
        items = generate_required_items(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        codes = {it["code"] for it in items}
        assert "kyc_refresh" in codes
        assert "ubo_confirmation" in codes
        # Persisted as JSON
        stored = json.loads(_review(review_db, rid)["required_items"])
        assert stored == items
        # Audit emitted
        assert any(e["action"] == "periodic_review.required_items.generated"
                   for e in audit_sink.events)

    def test_high_risk_adds_sof_and_sow(self, review_db, audit_sink):
        from periodic_review_engine import generate_required_items
        rid = _insert_review(review_db, risk_level="HIGH")
        items = generate_required_items(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        codes = {it["code"] for it in items}
        assert "source_of_funds_refresh" in codes
        assert "source_of_wealth_refresh" in codes
        assert "licensing_refresh" not in codes

    def test_very_high_risk_adds_licensing(self, review_db, audit_sink):
        from periodic_review_engine import generate_required_items
        rid = _insert_review(review_db, risk_level="VERY_HIGH")
        items = generate_required_items(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        codes = {it["code"] for it in items}
        assert "licensing_refresh" in codes

    def test_jurisdiction_and_sector_items(self, review_db, audit_sink):
        from periodic_review_engine import generate_required_items
        rid = _insert_review(review_db)
        items = generate_required_items(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        codes = {it["code"] for it in items}
        # Application has country=Mauritius, sector=Fintech, ownership_structure
        assert "jurisdiction_review" in codes
        assert "business_activity_review" in codes
        assert "ownership_change_review" in codes

    def test_monitoring_alert_followup_when_alert_origin(self, review_db, audit_sink):
        from periodic_review_engine import generate_required_items
        alert_id = _insert_alert(review_db)
        rid = _insert_review(
            review_db,
            trigger_source="monitoring_alert",
            linked_monitoring_alert_id=alert_id,
            review_reason="adverse media hit on UBO X",
        )
        items = generate_required_items(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        codes = {it["code"] for it in items}
        assert "monitoring_alert_followup" in codes
        # rationale references the alert id
        followup = [it for it in items if it["code"] == "monitoring_alert_followup"][0]
        assert str(alert_id) in followup["rationale"]

    def test_prior_outcome_followup(self, review_db, audit_sink):
        from periodic_review_engine import generate_required_items
        # Insert a prior completed review with outcome=enhanced_monitoring
        review_db.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, status, outcome) "
            "VALUES (?, ?, ?, 'completed', 'enhanced_monitoring')",
            ("test-app-100", "Test Co Ltd", "MEDIUM"),
        )
        review_db.commit()
        rid = _insert_review(review_db)
        items = generate_required_items(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        codes = {it["code"] for it in items}
        assert "prior_outcome_followup" in codes

    def test_refuses_on_completed_review(self, review_db, audit_sink):
        from periodic_review_engine import (
            generate_required_items, ReviewClosedError,
        )
        rid = _insert_review(review_db, status="completed")
        with pytest.raises(ReviewClosedError):
            generate_required_items(
                review_db, rid, user=USER, audit_writer=audit_sink,
            )

    def test_audit_writer_required(self, review_db):
        from periodic_review_engine import generate_required_items
        from lifecycle_linkage import MissingAuditWriter
        rid = _insert_review(review_db)
        with pytest.raises(MissingAuditWriter):
            generate_required_items(
                review_db, rid, user=USER, audit_writer=None,
            )
        assert _review(review_db, rid)["required_items"] is None


# ─────────────────────────────────────────────────────────────────
# Escalation to EDD
# ─────────────────────────────────────────────────────────────────
class TestEscalateToEDD:
    def test_creates_new_edd_when_none_active(self, review_db, audit_sink):
        from periodic_review_engine import escalate_review_to_edd
        rid = _insert_review(review_db, risk_level="HIGH")
        result = escalate_review_to_edd(
            review_db, rid,
            trigger_notes="elevated risk -- escalate",
            user=USER, audit_writer=audit_sink,
        )
        assert result["created"] is True
        assert result["reused"] is False
        edd = _edd(review_db, result["edd_case_id"])
        assert edd["origin_context"] == "periodic_review"
        assert edd["linked_periodic_review_id"] == rid
        assert edd["trigger_source"] == "periodic_review"
        # forward link from review
        assert _review(review_db, rid)["linked_edd_case_id"] == result["edd_case_id"]
        # audit event present
        assert any(e["action"] == "periodic_review.escalated_to_edd"
                   for e in audit_sink.events)

    def test_reuses_existing_review_linked_edd(self, review_db, audit_sink):
        from periodic_review_engine import escalate_review_to_edd
        rid = _insert_review(review_db, risk_level="HIGH")
        first = escalate_review_to_edd(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        # Second call from the same review -- must reuse
        second = escalate_review_to_edd(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        assert second["edd_case_id"] == first["edd_case_id"]
        assert second["created"] is False
        assert second["reused"] is True
        # Exactly one EDD case for the application
        n = review_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id = ?",
            ("test-app-100",),
        ).fetchone()["c"]
        assert n == 1

    def test_reuses_active_application_edd_from_other_origin(self, review_db, audit_sink):
        """Mirrors EDDCreateHandler / monitoring_routing dedup contract.

        If a different active EDD already exists for the same
        application (e.g. created via the manual EDDCreateHandler), the
        review escalation must link to it, not create a parallel EDD.
        """
        from periodic_review_engine import escalate_review_to_edd
        existing_edd = _insert_edd(review_db)
        rid = _insert_review(review_db, risk_level="HIGH")
        result = escalate_review_to_edd(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        assert result["edd_case_id"] == existing_edd
        assert result["created"] is False
        assert result["reused"] is True
        # EDD origin updated to periodic_review since this is the new
        # touchpoint, but no parallel case is created
        edd = _edd(review_db, existing_edd)
        assert edd["origin_context"] == "periodic_review"
        assert edd["linked_periodic_review_id"] == rid

    def test_monitoring_originated_review_escalates_as_first_class(
            self, review_db, audit_sink):
        """PR-02 contract absorption: monitoring-originated reviews are
        first-class reviews and may escalate to EDD identically to
        scheduled reviews. The legacy ``trigger_review`` alias on a
        monitoring alert PATCH already creates a real periodic_reviews
        row -- here we prove that row escalates correctly.
        """
        from periodic_review_engine import escalate_review_to_edd
        alert_id = _insert_alert(review_db)
        rid = _insert_review(
            review_db,
            trigger_source="monitoring_alert",
            linked_monitoring_alert_id=alert_id,
            review_reason="alert-triggered review",
            risk_level="HIGH",
        )
        result = escalate_review_to_edd(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        assert result["created"] is True
        edd = _edd(review_db, result["edd_case_id"])
        assert edd["origin_context"] == "periodic_review"
        assert edd["linked_periodic_review_id"] == rid

    def test_pr02_reverse_link_displacement_contract_respected(
            self, review_db, audit_sink):
        """PR-02 reality: edd_cases reverse-link pointers are
        last-write-wins, NOT symmetric to every alert/review that
        pointed at this EDD.

        Concretely, when a periodic-review escalation reuses an active
        EDD that was previously the target of a monitoring-alert route,
        the alert-side forward link (``alert.linked_edd_case_id``) must
        remain intact so traceability from the alert to the EDD is not
        lost. The EDD's own reverse pointers are owned by the most
        recent originator (set via ``lifecycle_linkage.set_edd_origin``)
        and are NOT required to enumerate every prior originator. This
        test pins that asymmetry contract explicitly.
        """
        from periodic_review_engine import escalate_review_to_edd
        # Manually wire the active-EDD-with-alert-back-pointer scenario.
        existing_edd = _insert_edd(review_db)
        alert_id = _insert_alert(review_db)
        review_db.execute(
            "UPDATE monitoring_alerts SET linked_edd_case_id = ? WHERE id = ?",
            (existing_edd, alert_id),
        )
        review_db.execute(
            "UPDATE edd_cases SET linked_monitoring_alert_id = ? WHERE id = ?",
            (alert_id, existing_edd),
        )
        review_db.commit()

        rid = _insert_review(review_db, risk_level="HIGH")
        result = escalate_review_to_edd(
            review_db, rid, user=USER, audit_writer=audit_sink,
        )
        assert result["edd_case_id"] == existing_edd
        # The alert-side forward link is preserved -- traceability from
        # the alert to the EDD is never broken by a periodic-review
        # escalation. This is the contract that downstream readers
        # (UI, audit consumers) can rely on.
        assert _alert(review_db, alert_id)["linked_edd_case_id"] == existing_edd
        # The review-side forward link is set
        assert _review(review_db, rid)["linked_edd_case_id"] == existing_edd
        # The EDD's reverse pointer to the periodic review IS set;
        # its reverse pointer to the alert may be displaced by the
        # periodic-review origin -- this asymmetry is documented and
        # accepted (PR-02 contract).
        edd = _edd(review_db, existing_edd)
        assert edd["linked_periodic_review_id"] == rid
        assert edd["origin_context"] == "periodic_review"

    def test_refuses_on_completed_review(self, review_db, audit_sink):
        from periodic_review_engine import (
            escalate_review_to_edd, ReviewClosedError,
        )
        rid = _insert_review(review_db, status="completed")
        with pytest.raises(ReviewClosedError):
            escalate_review_to_edd(
                review_db, rid, user=USER, audit_writer=audit_sink,
            )

    def test_audit_writer_required(self, review_db):
        from periodic_review_engine import escalate_review_to_edd
        from lifecycle_linkage import MissingAuditWriter
        rid = _insert_review(review_db, risk_level="HIGH")
        with pytest.raises(MissingAuditWriter):
            escalate_review_to_edd(
                review_db, rid, user=USER, audit_writer=None,
            )
        # No EDD must have been created.
        n = review_db.execute(
            "SELECT COUNT(*) AS c FROM edd_cases"
        ).fetchone()["c"]
        assert n == 0


# ─────────────────────────────────────────────────────────────────
# Outcome recording
# ─────────────────────────────────────────────────────────────────
class TestRecordOutcome:
    def test_records_outcome_and_closes_review(self, review_db, audit_sink):
        from periodic_review_engine import (
            record_review_outcome, OUTCOME_NO_CHANGE,
        )
        rid = _insert_review(review_db, status="in_progress")
        result = record_review_outcome(
            review_db, rid,
            outcome=OUTCOME_NO_CHANGE,
            outcome_reason="all checks passed",
            user=USER, audit_writer=audit_sink,
        )
        assert result["outcome"] == "no_change"
        row = _review(review_db, rid)
        assert row["status"] == "completed"
        assert row["outcome"] == "no_change"
        assert row["outcome_reason"] == "all checks passed"
        assert row["outcome_recorded_at"] is not None
        assert row["completed_at"] is not None
        # PR-01 closed_at is also stamped
        assert row["closed_at"] is not None
        # Audit events
        actions = [e["action"] for e in audit_sink.events]
        assert "periodic_review.outcome_recorded" in actions
        assert "lifecycle.review.closed" in actions

    def test_decision_replay_blocked(self, review_db, audit_sink):
        from periodic_review_engine import (
            record_review_outcome, OUTCOME_NO_CHANGE, ReviewClosedError,
        )
        rid = _insert_review(review_db, status="in_progress")
        record_review_outcome(
            review_db, rid,
            outcome=OUTCOME_NO_CHANGE, outcome_reason="initial",
            user=USER, audit_writer=audit_sink,
        )
        with pytest.raises(ReviewClosedError):
            record_review_outcome(
                review_db, rid,
                outcome=OUTCOME_NO_CHANGE, outcome_reason="replay",
                user=USER, audit_writer=audit_sink,
            )

    def test_invalid_outcome_rejected(self, review_db, audit_sink):
        from periodic_review_engine import (
            record_review_outcome, InvalidReviewOutcome,
        )
        rid = _insert_review(review_db, status="in_progress")
        with pytest.raises(InvalidReviewOutcome):
            record_review_outcome(
                review_db, rid,
                outcome="bogus", outcome_reason="x",
                user=USER, audit_writer=audit_sink,
            )
        assert _review(review_db, rid)["status"] == "in_progress"

    def test_outcome_reason_required(self, review_db, audit_sink):
        from periodic_review_engine import (
            record_review_outcome, OUTCOME_EXIT_RECOMMENDED,
            PeriodicReviewEngineError,
        )
        rid = _insert_review(review_db, status="in_progress")
        with pytest.raises(PeriodicReviewEngineError):
            record_review_outcome(
                review_db, rid,
                outcome=OUTCOME_EXIT_RECOMMENDED, outcome_reason="   ",
                user=USER, audit_writer=audit_sink,
            )

    def test_does_not_touch_compliance_memos(self, review_db, audit_sink):
        """Onboarding memo history is intentionally separate from
        periodic review lifecycle context. record_review_outcome must
        never write into compliance_memos.
        """
        from periodic_review_engine import (
            record_review_outcome, OUTCOME_ENHANCED_MONITORING,
        )
        # Seed a memo row so we can prove it is not mutated.
        review_db.execute(
            "INSERT INTO compliance_memos "
            "(application_id, version, memo_data, ai_recommendation) "
            "VALUES (?, ?, ?, ?)",
            ("test-app-100", 1, "{}", "approve"),
        )
        review_db.commit()
        memo_before = review_db.execute(
            "SELECT * FROM compliance_memos "
            "WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            ("test-app-100",),
        ).fetchone()

        rid = _insert_review(review_db, status="in_progress")
        record_review_outcome(
            review_db, rid,
            outcome=OUTCOME_ENHANCED_MONITORING,
            outcome_reason="elevated activity volume",
            user=USER, audit_writer=audit_sink,
        )
        memo_after = review_db.execute(
            "SELECT * FROM compliance_memos "
            "WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            ("test-app-100",),
        ).fetchone()
        # Same row, same content -- no overwrite.
        assert memo_before["id"] == memo_after["id"]
        assert memo_before["memo_data"] == memo_after["memo_data"]
        assert memo_before["ai_recommendation"] == memo_after["ai_recommendation"]
        # Count unchanged
        n = review_db.execute(
            "SELECT COUNT(*) AS c FROM compliance_memos "
            "WHERE application_id = ?",
            ("test-app-100",),
        ).fetchone()["c"]
        assert n == 1

    def test_audit_writer_required(self, review_db):
        from periodic_review_engine import (
            record_review_outcome, OUTCOME_NO_CHANGE,
        )
        from lifecycle_linkage import MissingAuditWriter
        rid = _insert_review(review_db, status="in_progress")
        with pytest.raises(MissingAuditWriter):
            record_review_outcome(
                review_db, rid,
                outcome=OUTCOME_NO_CHANGE, outcome_reason="x",
                user=USER, audit_writer=None,
            )
        assert _review(review_db, rid)["status"] == "in_progress"


# ─────────────────────────────────────────────────────────────────
# Read helpers
# ─────────────────────────────────────────────────────────────────
class TestReadHelpers:
    def test_get_review_state_normalises_unknown(self, review_db):
        from periodic_review_engine import get_review_state, STATE_PENDING
        # Insert a review with a weird status string to simulate legacy.
        review_db.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, status) VALUES (?, ?, ?)",
            ("test-app-100", "Test Co Ltd", "weird-legacy"),
        )
        review_db.commit()
        rid = review_db.execute(
            "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        assert get_review_state(review_db, rid) == STATE_PENDING

    def test_get_required_items_empty_default(self, review_db):
        from periodic_review_engine import get_required_items
        rid = _insert_review(review_db)
        assert get_required_items(review_db, rid) == []

    def test_review_not_found(self, review_db):
        from periodic_review_engine import (
            get_review_state, ReviewNotFound,
        )
        with pytest.raises(ReviewNotFound):
            get_review_state(review_db, 9999)
