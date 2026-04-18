"""
Tests for edd_memo_integration -- PR-04.

Verifies:

* :func:`resolve_active_memo_context` deterministically routes EDD
  cases to the correct memo context, honoring PR-01 origin_context and
  PR-02 reverse-link displacement realities;

* structured findings can be created and updated via
  :func:`set_edd_findings` with strict input validation;

* :func:`attach_edd_findings_to_memo_context` writes a single audited
  attachment row, refuses to attach without findings, and is idempotent
  on the same context;

* re-resolving and re-attaching after the EDD context changes
  (onboarding -> periodic review) creates a NEW attachment instead of
  mutating the previous one, preserving onboarding memo history;

* periodic-review and onboarding contexts remain DISJOINT (no
  cross-context bleed via the read helpers);

* audit-writer is REQUIRED for every mutating helper
  (:class:`MissingAuditWriter` raised BEFORE any DB write);

* ``compliance_memos`` rows are NEVER mutated by this module;

* PR-03a ``periodic_reviews.outcome`` is the field consulted, not the
  legacy ``decision`` column;

* monitoring-originated EDD without a review link is routed to the
  onboarding context (no disconnected EDD memo universe is created);

* :class:`MemoContextResolutionError` is raised in the documented
  under-specified case (origin='periodic_review' without explicit
  linkage), never silently guessed.

Test fixture mirrors tests/test_periodic_review_engine.py for
consistency.
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
def edd_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with repository schema + migrations 008/009/010."""
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
                "test-app-200", "APP-200", "Test Co Ltd",
                "Mauritius", "Fintech", "single-tier", "MEDIUM",
                "approved",
            ),
        )
    except Exception:
        conn.execute(
            "INSERT OR IGNORE INTO applications (id, ref, company_name) VALUES (?, ?, ?)",
            ("test-app-200", "APP-200", "Test Co Ltd"),
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


# ─────────────────────────────────────────────────────────────────
# Insert helpers
# ─────────────────────────────────────────────────────────────────
def _insert_edd(conn, *, application_id="test-app-200",
                client_name="Test Co Ltd", stage="triggered",
                origin_context=None,
                linked_periodic_review_id=None,
                linked_monitoring_alert_id=None):
    conn.execute(
        "INSERT INTO edd_cases "
        "(application_id, client_name, stage, origin_context, "
        " linked_periodic_review_id, linked_monitoring_alert_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (application_id, client_name, stage, origin_context,
         linked_periodic_review_id, linked_monitoring_alert_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM edd_cases ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


def _insert_review(conn, *, application_id="test-app-200",
                   client_name="Test Co Ltd", risk_level="MEDIUM",
                   status="pending", trigger_source=None,
                   linked_monitoring_alert_id=None):
    conn.execute(
        "INSERT INTO periodic_reviews "
        "(application_id, client_name, risk_level, status, "
        " trigger_source, linked_monitoring_alert_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (application_id, client_name, risk_level, status,
         trigger_source, linked_monitoring_alert_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


def _insert_alert(conn, *, application_id="test-app-200",
                  status="open", linked_periodic_review_id=None):
    conn.execute(
        "INSERT INTO monitoring_alerts "
        "(application_id, client_name, alert_type, severity, status, "
        " linked_periodic_review_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (application_id, "Test Co Ltd", "adverse_media", "medium",
         status, linked_periodic_review_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


def _insert_compliance_memo(conn, *, application_id="test-app-200",
                            version=1):
    conn.execute(
        "INSERT INTO compliance_memos "
        "(application_id, version, memo_data) VALUES (?, ?, ?)",
        (application_id, version, json.dumps({"summary": "test memo"})),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM compliance_memos ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]


# ─────────────────────────────────────────────────────────────────
# Migration 010 schema
# ─────────────────────────────────────────────────────────────────
class TestMigration010Schema:
    def test_edd_findings_table_exists(self, edd_db):
        rows = edd_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='edd_findings'"
        ).fetchall()
        assert len(rows) == 1

    def test_edd_memo_attachments_table_exists(self, edd_db):
        rows = edd_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='edd_memo_attachments'"
        ).fetchall()
        assert len(rows) == 1

    def test_compliance_memos_unmodified(self, edd_db):
        """PR-04 must NOT add columns to compliance_memos. The existing
        identity model (per-application per-version) is preserved."""
        cols = [
            r["name"] for r in edd_db.execute(
                "PRAGMA table_info(compliance_memos)"
            ).fetchall()
        ]
        # Sanity: known existing columns are still there.
        assert "application_id" in cols
        assert "version" in cols
        # Defensive: PR-04 must NOT introduce these on compliance_memos.
        assert "linked_edd_case_id" not in cols
        assert "memo_context_kind" not in cols


# ─────────────────────────────────────────────────────────────────
# Active memo context resolution
# ─────────────────────────────────────────────────────────────────
class TestResolveActiveMemoContext:
    def test_onboarding_origin_routes_to_onboarding(self, edd_db):
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_ONBOARDING,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_ONBOARDING
        assert ctx["application_id"] == "test-app-200"
        assert ctx["periodic_review_id"] is None
        # No memo yet -> memo_id is None but the context is still resolvable.
        assert ctx["memo_id"] is None

    def test_onboarding_origin_picks_latest_memo_id(self, edd_db):
        from edd_memo_integration import resolve_active_memo_context
        _insert_compliance_memo(edd_db, version=1)
        latest = _insert_compliance_memo(edd_db, version=2)
        eid = _insert_edd(edd_db, origin_context="onboarding")
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["memo_id"] == latest

    def test_explicit_review_link_wins_over_origin(self, edd_db):
        """Rule 1: explicit linked_periodic_review_id is the strongest
        signal even when origin_context disagrees."""
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        rid = _insert_review(edd_db)
        eid = _insert_edd(
            edd_db, origin_context="onboarding",
            linked_periodic_review_id=rid,
        )
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_PERIODIC_REVIEW
        assert ctx["periodic_review_id"] == rid
        assert ctx["memo_id"] is None
        assert "linked_periodic_review_id" in ctx["resolution_reason"]

    def test_periodic_review_origin_without_link_raises(self, edd_db):
        """Rule 2: under-specified -- must NOT silently guess."""
        from edd_memo_integration import (
            resolve_active_memo_context, MemoContextResolutionError,
        )
        eid = _insert_edd(edd_db, origin_context="periodic_review")
        with pytest.raises(MemoContextResolutionError):
            resolve_active_memo_context(edd_db, eid)

    def test_review_link_to_missing_review_raises(self, edd_db):
        """Data-integrity guard: a stale linked_periodic_review_id must
        not silently fall back."""
        from edd_memo_integration import (
            resolve_active_memo_context, MemoContextResolutionError,
        )
        eid = _insert_edd(
            edd_db, origin_context="periodic_review",
            linked_periodic_review_id=99999,
        )
        with pytest.raises(MemoContextResolutionError):
            resolve_active_memo_context(edd_db, eid)

    def test_monitoring_alert_with_review_routes_to_review(self, edd_db):
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        rid = _insert_review(edd_db)
        aid = _insert_alert(edd_db, linked_periodic_review_id=rid)
        eid = _insert_edd(
            edd_db, origin_context="monitoring_alert",
            linked_monitoring_alert_id=aid,
        )
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_PERIODIC_REVIEW
        assert ctx["periodic_review_id"] == rid

    def test_monitoring_alert_without_review_routes_to_onboarding(self, edd_db):
        """Documented contract: never create a disconnected EDD memo
        universe -- post-onboarding lifecycle EDD without a review
        link feeds the onboarding memo context."""
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_ONBOARDING,
        )
        aid = _insert_alert(edd_db)  # no review link
        eid = _insert_edd(
            edd_db, origin_context="monitoring_alert",
            linked_monitoring_alert_id=aid,
        )
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_ONBOARDING

    def test_null_origin_defaults_to_onboarding(self, edd_db):
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_ONBOARDING,
        )
        eid = _insert_edd(edd_db, origin_context=None)
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_ONBOARDING

    def test_change_request_origin_defaults_to_onboarding(self, edd_db):
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_ONBOARDING,
        )
        eid = _insert_edd(edd_db, origin_context="change_request")
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_ONBOARDING

    def test_missing_edd_raises(self, edd_db):
        from edd_memo_integration import (
            resolve_active_memo_context, EDDCaseNotFound,
        )
        with pytest.raises(EDDCaseNotFound):
            resolve_active_memo_context(edd_db, 99999)


# ─────────────────────────────────────────────────────────────────
# Structured findings -- create / update / read
# ─────────────────────────────────────────────────────────────────
class TestEDDFindings:
    def test_create_findings_minimal(self, edd_db, audit_sink):
        from edd_memo_integration import set_edd_findings, get_edd_findings
        eid = _insert_edd(edd_db, origin_context="onboarding")
        result = set_edd_findings(
            edd_db, eid,
            findings={"findings_summary": "All clean."},
            user=USER, audit_writer=audit_sink,
        )
        assert result["findings_summary"] == "All clean."
        assert result["key_concerns"] == []
        # Persisted.
        again = get_edd_findings(edd_db, eid)
        assert again["findings_summary"] == "All clean."
        # Audit.
        assert any(e["action"] == "edd.findings.created"
                   for e in audit_sink.events)

    def test_create_findings_full(self, edd_db, audit_sink):
        from edd_memo_integration import (
            set_edd_findings, RECOMMENDED_OUTCOME_APPROVE_WITH_CONDITIONS,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        result = set_edd_findings(
            edd_db, eid,
            findings={
                "findings_summary": "PEP exposure mitigated.",
                "key_concerns": ["PEP director", "Offshore SPV"],
                "mitigating_evidence": ["Independent audit", "Source of wealth"],
                "conditions": ["Quarterly transaction review"],
                "rationale": "Risk acceptable with enhanced monitoring.",
                "supporting_notes": [{"ref": "doc-1", "note": "Audit report"}],
                "recommended_outcome": RECOMMENDED_OUTCOME_APPROVE_WITH_CONDITIONS,
            },
            user=USER, audit_writer=audit_sink,
        )
        assert result["key_concerns"] == ["PEP director", "Offshore SPV"]
        assert result["conditions"] == ["Quarterly transaction review"]
        assert result["recommended_outcome"] == "approve_with_conditions"

    def test_update_findings_emits_updated_event(self, edd_db, audit_sink):
        from edd_memo_integration import set_edd_findings
        eid = _insert_edd(edd_db, origin_context="onboarding")
        set_edd_findings(
            edd_db, eid,
            findings={"findings_summary": "v1", "rationale": "first"},
            user=USER, audit_writer=audit_sink,
        )
        set_edd_findings(
            edd_db, eid,
            findings={"findings_summary": "v2"},
            user=USER, audit_writer=audit_sink,
        )
        actions = [e["action"] for e in audit_sink.events]
        assert "edd.findings.created" in actions
        assert "edd.findings.updated" in actions
        # Partial update preserves untouched fields.
        from edd_memo_integration import get_edd_findings
        cur = get_edd_findings(edd_db, eid)
        assert cur["findings_summary"] == "v2"
        assert cur["rationale"] == "first"

    def test_update_carries_before_after_state(self, edd_db, audit_sink):
        from edd_memo_integration import set_edd_findings
        eid = _insert_edd(edd_db, origin_context="onboarding")
        set_edd_findings(
            edd_db, eid,
            findings={"findings_summary": "v1"},
            user=USER, audit_writer=audit_sink,
        )
        set_edd_findings(
            edd_db, eid,
            findings={"findings_summary": "v2"},
            user=USER, audit_writer=audit_sink,
        )
        upd = [e for e in audit_sink.events
               if e["action"] == "edd.findings.updated"][0]
        assert upd["before_state"]["findings_summary"] == "v1"
        assert upd["after_state"]["findings_summary"] == "v2"

    def test_invalid_recommended_outcome_rejected(self, edd_db, audit_sink):
        from edd_memo_integration import (
            set_edd_findings, FindingsValidationError,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        with pytest.raises(FindingsValidationError):
            set_edd_findings(
                edd_db, eid,
                findings={"recommended_outcome": "totally_made_up"},
                user=USER, audit_writer=audit_sink,
            )

    def test_invalid_list_field_rejected(self, edd_db, audit_sink):
        from edd_memo_integration import (
            set_edd_findings, FindingsValidationError,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        with pytest.raises(FindingsValidationError):
            set_edd_findings(
                edd_db, eid,
                findings={"key_concerns": "not a list"},
                user=USER, audit_writer=audit_sink,
            )

    def test_invalid_text_field_rejected(self, edd_db, audit_sink):
        from edd_memo_integration import (
            set_edd_findings, FindingsValidationError,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        with pytest.raises(FindingsValidationError):
            set_edd_findings(
                edd_db, eid,
                findings={"findings_summary": 12345},
                user=USER, audit_writer=audit_sink,
            )

    def test_findings_for_missing_edd_raises(self, edd_db, audit_sink):
        from edd_memo_integration import (
            set_edd_findings, EDDCaseNotFound,
        )
        with pytest.raises(EDDCaseNotFound):
            set_edd_findings(
                edd_db, 99999,
                findings={"findings_summary": "nope"},
                user=USER, audit_writer=audit_sink,
            )

    def test_audit_writer_required(self, edd_db):
        from edd_memo_integration import set_edd_findings
        from lifecycle_linkage import MissingAuditWriter
        eid = _insert_edd(edd_db, origin_context="onboarding")
        with pytest.raises(MissingAuditWriter):
            set_edd_findings(
                edd_db, eid,
                findings={"findings_summary": "x"},
                user=USER, audit_writer=None,
            )
        # No row was written.
        from edd_memo_integration import get_edd_findings
        assert get_edd_findings(edd_db, eid) is None

    def test_get_findings_returns_none_when_absent(self, edd_db):
        from edd_memo_integration import get_edd_findings
        eid = _insert_edd(edd_db, origin_context="onboarding")
        assert get_edd_findings(edd_db, eid) is None


# ─────────────────────────────────────────────────────────────────
# Attachment to memo context
# ─────────────────────────────────────────────────────────────────
class TestAttachEDDFindingsToMemoContext:
    def _seed_findings(self, edd_db, audit_sink, eid):
        from edd_memo_integration import set_edd_findings
        set_edd_findings(
            edd_db, eid,
            findings={
                "findings_summary": "X",
                "recommended_outcome": "approve",
            },
            user=USER, audit_writer=audit_sink,
        )

    def test_attach_to_onboarding_context(self, edd_db, audit_sink):
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context, MEMO_CONTEXT_ONBOARDING,
        )
        memo_id = _insert_compliance_memo(edd_db)
        eid = _insert_edd(edd_db, origin_context="onboarding")
        self._seed_findings(edd_db, audit_sink, eid)
        out = attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        assert out["created"] is True
        assert out["reused"] is False
        att = out["attachment"]
        assert att["memo_context_kind"] == MEMO_CONTEXT_ONBOARDING
        assert att["memo_id"] == memo_id
        assert att["periodic_review_id"] is None
        assert att["application_id"] == "test-app-200"
        assert any(e["action"] == "edd.memo_context.attached"
                   for e in audit_sink.events)

    def test_attach_to_periodic_review_context(self, edd_db, audit_sink):
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        rid = _insert_review(edd_db)
        eid = _insert_edd(
            edd_db, origin_context="periodic_review",
            linked_periodic_review_id=rid,
        )
        self._seed_findings(edd_db, audit_sink, eid)
        out = attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        assert out["created"] is True
        att = out["attachment"]
        assert att["memo_context_kind"] == MEMO_CONTEXT_PERIODIC_REVIEW
        assert att["periodic_review_id"] == rid
        assert att["memo_id"] is None

    def test_attach_is_idempotent_for_same_context(self, edd_db, audit_sink):
        from edd_memo_integration import attach_edd_findings_to_memo_context
        eid = _insert_edd(edd_db, origin_context="onboarding")
        self._seed_findings(edd_db, audit_sink, eid)
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        second = attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        assert second["created"] is False
        assert second["reused"] is True
        # Only ONE attached event was emitted.
        attached_events = [
            e for e in audit_sink.events
            if e["action"] == "edd.memo_context.attached"
        ]
        assert len(attached_events) == 1

    def test_re_resolve_after_context_change_creates_new_attachment(
        self, edd_db, audit_sink,
    ):
        """If the EDD context changes (onboarding -> periodic review),
        attaching again creates a NEW attachment row -- the old one is
        preserved for audit history (no overwrite of onboarding memo
        linkage)."""
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context,
            get_memo_context_attachments,
            MEMO_CONTEXT_ONBOARDING, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        memo_id = _insert_compliance_memo(edd_db)
        eid = _insert_edd(edd_db, origin_context="onboarding")
        self._seed_findings(edd_db, audit_sink, eid)
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        # Now simulate re-linkage to a periodic review.
        rid = _insert_review(edd_db)
        edd_db.execute(
            "UPDATE edd_cases SET linked_periodic_review_id = ? WHERE id = ?",
            (rid, eid),
        )
        edd_db.commit()
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        onb = get_memo_context_attachments(
            edd_db, kind=MEMO_CONTEXT_ONBOARDING, memo_id=memo_id,
        )
        rev = get_memo_context_attachments(
            edd_db, kind=MEMO_CONTEXT_PERIODIC_REVIEW, periodic_review_id=rid,
        )
        # Both attachments are visible -- the onboarding linkage was
        # NOT silently overwritten.
        assert len(onb) == 1
        assert len(rev) == 1
        assert onb[0]["edd_case_id"] == eid
        assert rev[0]["edd_case_id"] == eid

    def test_attach_without_findings_raises(self, edd_db, audit_sink):
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context, AttachmentValidationError,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        with pytest.raises(AttachmentValidationError):
            attach_edd_findings_to_memo_context(
                edd_db, eid, user=USER, audit_writer=audit_sink,
            )

    def test_attach_audit_writer_required(self, edd_db, audit_sink):
        from edd_memo_integration import attach_edd_findings_to_memo_context
        from lifecycle_linkage import MissingAuditWriter
        eid = _insert_edd(edd_db, origin_context="onboarding")
        # seed findings legitimately
        from edd_memo_integration import set_edd_findings
        set_edd_findings(
            edd_db, eid,
            findings={"findings_summary": "x"},
            user=USER, audit_writer=audit_sink,
        )
        with pytest.raises(MissingAuditWriter):
            attach_edd_findings_to_memo_context(
                edd_db, eid, user=USER, audit_writer=None,
            )

    def test_attach_propagates_resolution_error(self, edd_db, audit_sink):
        """Origin='periodic_review' without explicit link must surface as
        MemoContextResolutionError, never silently attached to onboarding."""
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context, MemoContextResolutionError,
            set_edd_findings,
        )
        eid = _insert_edd(edd_db, origin_context="periodic_review")
        set_edd_findings(
            edd_db, eid, findings={"findings_summary": "x"},
            user=USER, audit_writer=audit_sink,
        )
        with pytest.raises(MemoContextResolutionError):
            attach_edd_findings_to_memo_context(
                edd_db, eid, user=USER, audit_writer=audit_sink,
            )

    def test_attach_does_not_mutate_compliance_memos(self, edd_db, audit_sink):
        """Onboarding memo identity is preserved -- attaching must NOT
        modify any compliance_memos row."""
        from edd_memo_integration import attach_edd_findings_to_memo_context
        memo_id = _insert_compliance_memo(edd_db)
        before = edd_db.execute(
            "SELECT * FROM compliance_memos WHERE id = ?", (memo_id,),
        ).fetchone()
        before_dict = dict(before)
        eid = _insert_edd(edd_db, origin_context="onboarding")
        self._seed_findings(edd_db, audit_sink, eid)
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        after = edd_db.execute(
            "SELECT * FROM compliance_memos WHERE id = ?", (memo_id,),
        ).fetchone()
        assert dict(after) == before_dict


# ─────────────────────────────────────────────────────────────────
# Detach
# ─────────────────────────────────────────────────────────────────
class TestDetachEDDFindings:
    def test_detach_marks_row_and_emits_audit(self, edd_db, audit_sink):
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context,
            detach_edd_findings_from_memo_context,
            set_edd_findings,
        )
        eid = _insert_edd(edd_db, origin_context="onboarding")
        set_edd_findings(
            edd_db, eid, findings={"findings_summary": "x"},
            user=USER, audit_writer=audit_sink,
        )
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        detached = detach_edd_findings_from_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        assert len(detached) == 1
        assert detached[0]["detached_at"] is not None
        assert any(e["action"] == "edd.memo_context.detached"
                   for e in audit_sink.events)

    def test_detach_is_noop_when_nothing_attached(self, edd_db, audit_sink):
        from edd_memo_integration import detach_edd_findings_from_memo_context
        eid = _insert_edd(edd_db, origin_context="onboarding")
        detached = detach_edd_findings_from_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        assert detached == []
        # No audit event for noop.
        assert not any(e["action"] == "edd.memo_context.detached"
                       for e in audit_sink.events)

    def test_detach_audit_writer_required(self, edd_db):
        from edd_memo_integration import detach_edd_findings_from_memo_context
        from lifecycle_linkage import MissingAuditWriter
        eid = _insert_edd(edd_db, origin_context="onboarding")
        with pytest.raises(MissingAuditWriter):
            detach_edd_findings_from_memo_context(
                edd_db, eid, user=USER, audit_writer=None,
            )


# ─────────────────────────────────────────────────────────────────
# Read helpers + onboarding/review separation
# ─────────────────────────────────────────────────────────────────
class TestReadHelpers:
    def test_get_memo_context_findings_returns_attached_findings(
        self, edd_db, audit_sink,
    ):
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context,
            get_memo_context_findings,
            set_edd_findings,
            MEMO_CONTEXT_ONBOARDING,
        )
        memo_id = _insert_compliance_memo(edd_db)
        eid = _insert_edd(edd_db, origin_context="onboarding")
        set_edd_findings(
            edd_db, eid,
            findings={
                "findings_summary": "Detailed",
                "key_concerns": ["a", "b"],
            },
            user=USER, audit_writer=audit_sink,
        )
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        items = get_memo_context_findings(
            edd_db, kind=MEMO_CONTEXT_ONBOARDING, memo_id=memo_id,
        )
        assert len(items) == 1
        assert items[0]["findings_summary"] == "Detailed"
        assert items[0]["key_concerns"] == ["a", "b"]
        assert items[0]["attachment"]["edd_case_id"] == eid

    def test_onboarding_and_review_contexts_are_disjoint(
        self, edd_db, audit_sink,
    ):
        """An EDD attached to onboarding must not surface under the
        periodic-review context, and vice versa."""
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context,
            get_memo_context_findings,
            set_edd_findings,
            MEMO_CONTEXT_ONBOARDING, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        memo_id = _insert_compliance_memo(edd_db)
        # Onboarding-context EDD
        eid_onb = _insert_edd(edd_db, origin_context="onboarding")
        set_edd_findings(
            edd_db, eid_onb,
            findings={"findings_summary": "onb-finding"},
            user=USER, audit_writer=audit_sink,
        )
        attach_edd_findings_to_memo_context(
            edd_db, eid_onb, user=USER, audit_writer=audit_sink,
        )
        # Periodic-review-context EDD on the same application.
        rid = _insert_review(edd_db)
        eid_rev = _insert_edd(
            edd_db, origin_context="periodic_review",
            linked_periodic_review_id=rid,
        )
        set_edd_findings(
            edd_db, eid_rev,
            findings={"findings_summary": "rev-finding"},
            user=USER, audit_writer=audit_sink,
        )
        attach_edd_findings_to_memo_context(
            edd_db, eid_rev, user=USER, audit_writer=audit_sink,
        )

        onb_items = get_memo_context_findings(
            edd_db, kind=MEMO_CONTEXT_ONBOARDING, memo_id=memo_id,
        )
        rev_items = get_memo_context_findings(
            edd_db, kind=MEMO_CONTEXT_PERIODIC_REVIEW,
            periodic_review_id=rid,
        )
        assert {i["findings_summary"] for i in onb_items} == {"onb-finding"}
        assert {i["findings_summary"] for i in rev_items} == {"rev-finding"}

    def test_detached_attachments_excluded_by_default(
        self, edd_db, audit_sink,
    ):
        from edd_memo_integration import (
            attach_edd_findings_to_memo_context,
            detach_edd_findings_from_memo_context,
            get_memo_context_attachments,
            set_edd_findings,
            MEMO_CONTEXT_ONBOARDING,
        )
        memo_id = _insert_compliance_memo(edd_db)
        eid = _insert_edd(edd_db, origin_context="onboarding")
        set_edd_findings(
            edd_db, eid, findings={"findings_summary": "x"},
            user=USER, audit_writer=audit_sink,
        )
        attach_edd_findings_to_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        detach_edd_findings_from_memo_context(
            edd_db, eid, user=USER, audit_writer=audit_sink,
        )
        active = get_memo_context_attachments(
            edd_db, kind=MEMO_CONTEXT_ONBOARDING, memo_id=memo_id,
        )
        all_rows = get_memo_context_attachments(
            edd_db, kind=MEMO_CONTEXT_ONBOARDING, memo_id=memo_id,
            include_detached=True,
        )
        assert active == []
        assert len(all_rows) == 1
        assert all_rows[0]["detached_at"] is not None

    def test_invalid_kind_rejected(self, edd_db):
        from edd_memo_integration import (
            get_memo_context_attachments, AttachmentValidationError,
        )
        with pytest.raises(AttachmentValidationError):
            get_memo_context_attachments(edd_db, kind="garbage")


# ─────────────────────────────────────────────────────────────────
# Lifecycle integration smoke -- end-to-end via PR-01 helpers
# ─────────────────────────────────────────────────────────────────
class TestLifecycleIntegrationSmoke:
    def test_set_edd_origin_then_resolve_routes_correctly(
        self, edd_db, audit_sink,
    ):
        """Use the real PR-01 lifecycle_linkage helper to set origin,
        then verify resolve_active_memo_context picks it up."""
        import lifecycle_linkage as ll
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        rid = _insert_review(edd_db)
        eid = _insert_edd(edd_db, origin_context=None)
        ll.set_edd_origin(
            edd_db, eid,
            origin_context="periodic_review",
            linked_periodic_review_id=rid,
            user=USER, audit_writer=audit_sink,
        )
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_PERIODIC_REVIEW
        assert ctx["periodic_review_id"] == rid

    def test_pr03_outcome_is_authoritative_not_decision(
        self, edd_db, audit_sink,
    ):
        """PR-03a: read ``periodic_reviews.outcome`` (authoritative),
        never ``decision`` (legacy). PR-04 does not co-write either,
        so verify resolution still works regardless of the legacy
        ``decision`` column being NULL."""
        from edd_memo_integration import (
            resolve_active_memo_context, MEMO_CONTEXT_PERIODIC_REVIEW,
        )
        rid = _insert_review(edd_db)
        # Simulate a PR-03 completed review with outcome set, decision NULL.
        edd_db.execute(
            "UPDATE periodic_reviews "
            "SET outcome = 'edd_required', "
            "    outcome_reason = 'High risk indicators', "
            "    status = 'completed' "
            "WHERE id = ?",
            (rid,),
        )
        edd_db.commit()
        eid = _insert_edd(
            edd_db, origin_context="periodic_review",
            linked_periodic_review_id=rid,
        )
        ctx = resolve_active_memo_context(edd_db, eid)
        assert ctx["kind"] == MEMO_CONTEXT_PERIODIC_REVIEW
        assert ctx["periodic_review_id"] == rid
