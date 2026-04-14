"""
Tests for the Change Management module.

Covers:
- DB migration (table creation)
- Change Alert lifecycle (create, status transitions, conversion)
- Change Request lifecycle (create, submit, approve, reject, implement)
- Status transition guards (valid + invalid)
- Materiality classification
- Profile versioning and conflict detection
- Role-based permission enforcement
- Document attachment
- Entity profile snapshot
- No silent overwrite of approved profile data
"""

import json
import os
import sys
import secrets
import pytest


def _get_cm():
    """Lazy-import change_management module."""
    import change_management as cm
    return cm


def _get_db_module():
    """Lazy-import db module."""
    import db as db_module
    return db_module


def _setup_test_data(raw_db):
    """Create a test application with directors/UBOs."""
    app_id = f"test-cm-{secrets.token_hex(4)}"
    client_id = f"test-cl-{secrets.token_hex(4)}"

    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"test-{secrets.token_hex(3)}@test.com", "hash", "Test Company"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, "Test Company Ltd",
         "GB", "Financial Services", "Limited Company", "approved"),
    )
    raw_db.execute(
        """INSERT INTO directors (id, application_id, person_key, full_name, first_name, last_name, nationality, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "dir1", "John Smith", "John", "Smith", "GB", "1980-01-15"),
    )
    raw_db.execute(
        """INSERT INTO ubos (id, application_id, person_key, full_name, first_name, last_name, nationality, ownership_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (secrets.token_hex(8), app_id, "ubo1", "Jane Doe", "Jane", "Doe", "US", 75.0),
    )
    raw_db.commit()
    return app_id, client_id


class _DBWrapper:
    """Wrap raw sqlite3 connection to match cm module expectations."""
    def __init__(self, conn):
        self._conn = conn
        self.is_postgres = False
    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)
    def commit(self):
        self._conn.commit()
    def rollback(self):
        self._conn.rollback()
    def close(self):
        pass


# ============================================================================
# Pure logic tests (no DB needed)
# ============================================================================

class TestConstants:
    def test_alert_statuses(self):
        cm = _get_cm()
        assert len(cm.CHANGE_ALERT_STATUSES) == 7
        assert "new" in cm.CHANGE_ALERT_STATUSES

    def test_request_statuses(self):
        cm = _get_cm()
        assert len(cm.CHANGE_REQUEST_STATUSES) == 14
        assert "implemented" in cm.CHANGE_REQUEST_STATUSES

    def test_materiality_tiers(self):
        cm = _get_cm()
        assert cm.MATERIALITY_TIERS == ("tier1", "tier2", "tier3")

    def test_change_sources(self):
        cm = _get_cm()
        assert "portal_client" in cm.CHANGE_SOURCES
        assert "external_alert_conversion" in cm.CHANGE_SOURCES

    def test_change_channels(self):
        cm = _get_cm()
        assert "companies_house" in cm.CHANGE_CHANNELS


class TestMateriality:
    def test_tier1(self):
        cm = _get_cm()
        assert cm.classify_materiality("legal_name_change") == "tier1"
        assert cm.classify_materiality("director_change") == "tier1"
        assert cm.classify_materiality("ubo_change") == "tier1"

    def test_tier2(self):
        cm = _get_cm()
        assert cm.classify_materiality("same_country_address_change") == "tier2"
        assert cm.classify_materiality("signatory_change") == "tier2"

    def test_tier3(self):
        cm = _get_cm()
        assert cm.classify_materiality("contact_detail_update") == "tier3"
        assert cm.classify_materiality("typo_correction") == "tier3"

    def test_unknown_defaults_tier2(self):
        cm = _get_cm()
        assert cm.classify_materiality("some_unknown_type") == "tier2"


class TestDownstreamActions:
    def test_tier1_actions(self):
        cm = _get_cm()
        a = cm.get_downstream_actions("tier1")
        assert a["screening_required"] is True
        assert a["risk_review_required"] is True
        assert a["memo_addendum_hook"] is True

    def test_tier3_actions(self):
        cm = _get_cm()
        a = cm.get_downstream_actions("tier3")
        assert a["screening_required"] is False
        assert a["risk_review_required"] is False


class TestAlertTransitions:
    def test_valid_from_new(self):
        cm = _get_cm()
        assert cm.validate_alert_transition("new", "under_review") == (True, "")
        assert cm.validate_alert_transition("new", "dismissed") == (True, "")

    def test_invalid_from_new(self):
        cm = _get_cm()
        v, e = cm.validate_alert_transition("new", "converted_to_change_request")
        assert v is False

    def test_terminal_blocks(self):
        cm = _get_cm()
        for t in ("converted_to_change_request", "dismissed", "resolved_no_change"):
            v, _ = cm.validate_alert_transition(t, "under_review")
            assert v is False

    def test_unknown_status(self):
        cm = _get_cm()
        v, e = cm.validate_alert_transition("bogus", "new")
        assert v is False
        assert "Unknown" in e


class TestRequestTransitions:
    def test_draft_to_submitted(self):
        cm = _get_cm()
        assert cm.validate_request_transition("draft", "submitted") == (True, "")

    def test_invalid_draft_to_approved(self):
        cm = _get_cm()
        v, _ = cm.validate_request_transition("draft", "approved")
        assert v is False

    def test_approved_to_implemented(self):
        cm = _get_cm()
        assert cm.validate_request_transition("approved", "implemented") == (True, "")

    def test_terminal_blocks(self):
        cm = _get_cm()
        for t in ("rejected", "implemented", "cancelled", "superseded"):
            v, _ = cm.validate_request_transition(t, "draft")
            assert v is False


class TestRolePermissions:
    def test_admin_approve_tier1(self):
        cm = _get_cm()
        assert cm.check_role_permission("admin", "approve_tier1") == (True, "")

    def test_co_cannot_approve_tier1(self):
        cm = _get_cm()
        v, e = cm.check_role_permission("co", "approve_tier1")
        assert v is False

    def test_analyst_can_create(self):
        cm = _get_cm()
        v, e = cm.check_role_permission("analyst", "create_request")
        assert v is True
        assert e == ""

    def test_co_cannot_implement(self):
        cm = _get_cm()
        v, _ = cm.check_role_permission("co", "implement_change")
        assert v is False

    def test_unknown_action(self):
        cm = _get_cm()
        v, e = cm.check_role_permission("admin", "nonexistent_action")
        assert v is False


class TestIDGeneration:
    def test_alert_id(self):
        cm = _get_cm()
        assert cm.generate_change_alert_id().startswith("CA-")

    def test_request_id(self):
        cm = _get_cm()
        assert cm.generate_change_request_id().startswith("CR-")

    def test_version_id(self):
        cm = _get_cm()
        assert cm.generate_profile_version_id().startswith("PV-")

    def test_uniqueness(self):
        cm = _get_cm()
        ids = {cm.generate_change_alert_id() for _ in range(50)}
        assert len(ids) == 50

    def test_highest_materiality(self):
        cm = _get_cm()
        assert cm._highest_materiality(["tier1", "tier3"]) == "tier1"
        assert cm._highest_materiality(["tier2", "tier3"]) == "tier2"
        assert cm._highest_materiality([]) == "tier2"


# ============================================================================
# Database integration tests (use temp_db/db fixtures)
# ============================================================================

class TestDBIntegration:
    def test_migration_creates_tables(self, temp_db):
        db_mod = _get_db_module()
        conn = db_mod.get_db()
        try:
            for t in ("change_alerts", "change_requests", "change_request_items",
                      "change_request_documents", "change_request_reviews",
                      "entity_profile_versions"):
                assert db_mod._safe_table_exists(conn, t), f"Table {t} should exist"
        finally:
            conn.close()

    def test_create_alert(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "sco"}

        alert = cm.create_change_alert(
            db=wdb, application_id=app_id,
            alert_type="director_change", source_channel="companies_house",
            summary="New director detected",
            detected_changes={"directors": {"old": ["A"], "new": ["A", "B"]}},
            confidence=0.95, user=user,
        )
        assert alert["id"].startswith("CA-")
        assert alert["status"] == "new"
        assert alert["materiality"] == "tier1"

        row = db.execute("SELECT * FROM change_alerts WHERE id = ?", (alert["id"],)).fetchone()
        assert row is not None

    def test_alert_transition(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "sco"}

        alert = cm.create_change_alert(wdb, app_id, "ubo_change", "open_corporates",
                                        "Test", {}, user=user)
        ok, _ = cm.update_change_alert_status(wdb, alert["id"], "under_review", user)
        assert ok
        ok, _ = cm.update_change_alert_status(wdb, alert["id"], "dismissed", user, notes="FP")
        assert ok
        ok, _ = cm.update_change_alert_status(wdb, alert["id"], "under_review", user)
        assert not ok

    def test_convert_alert(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "sco"}

        alert = cm.create_change_alert(wdb, app_id, "director_change", "companies_house",
                                        "Test", {"dirs": {"old": "A", "new": "B"}}, user=user)
        cm.update_change_alert_status(wdb, alert["id"], "under_review", user)

        req, err = cm.convert_alert_to_request(wdb, alert["id"], user)
        assert req is not None, f"Failed: {err}"
        assert req["source"] == "external_alert_conversion"

        updated = cm.get_change_alert_detail(wdb, alert["id"])
        assert updated["status"] == "converted_to_change_request"

    def test_cannot_convert_dismissed(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "sco"}

        alert = cm.create_change_alert(wdb, app_id, "other", "backoffice", "T", {}, user=user)
        cm.update_change_alert_status(wdb, alert["id"], "dismissed", user)
        req, err = cm.convert_alert_to_request(wdb, alert["id"], user)
        assert req is None

    def test_create_request(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "co"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Old", "new_value": "New", "materiality": "tier1"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Name change", items, user)
        assert req["id"].startswith("CR-")
        assert req["status"] == "draft"
        assert req["materiality"] == "tier1"

        db_items = db.execute("SELECT * FROM change_request_items WHERE request_id = ?",
                              (req["id"],)).fetchall()
        assert len(db_items) == 1

    def test_submit_request(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "co"}

        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", _items, user)
        ok, _ = cm.submit_change_request(wdb, req["id"], user)
        assert ok
        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "submitted"

    def test_cannot_submit_twice(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "co"}

        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", _items, user)
        cm.submit_change_request(wdb, req["id"], user)
        ok, err = cm.submit_change_request(wdb, req["id"], user)
        assert not ok

    def test_full_lifecycle(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Test Company Ltd", "new_value": "New Name Ltd", "materiality": "tier1"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Name change", items, sco)
        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)

        ok, err = cm.approve_change_request(wdb, req["id"], sco, decision_notes="OK")
        assert ok, f"Approve failed: {err}"

        ok, err, vid = cm.implement_change_request(wdb, req["id"], sco)
        assert ok, f"Implement failed: {err}"
        assert vid is not None

        app = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert app["company_name"] == "New Name Ltd"

    def test_reject_request(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", _items, sco)
        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)

        ok, _ = cm.reject_change_request(wdb, req["id"], sco, decision_notes="No")
        assert ok

        detail = cm.get_change_request_detail(wdb, req["id"])
        assert detail["status"] == "rejected"

    def test_co_cannot_approve_tier1(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        co = {"sub": "co1", "name": "CO", "role": "co"}
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "director_change", "materiality": "tier1"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Dir change", items, sco)
        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)

        ok, err = cm.approve_change_request(wdb, req["id"], co)
        assert not ok
        assert "not permitted" in err

    def test_cannot_implement_unapproved(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", _items, sco)
        ok, err, _ = cm.implement_change_request(wdb, req["id"], sco)
        assert not ok
        assert "approved" in err.lower()

    def test_stale_version_conflict(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        cm._create_profile_version(wdb, app_id, "r1", {}, {"v": 1}, sco)
        wdb.commit()

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "X", "new_value": "Y", "materiality": "tier3"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", items, sco)

        cm._create_profile_version(wdb, app_id, "r2", {}, {"v": 2}, sco)
        wdb.commit()

        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)
        cm.approve_change_request(wdb, req["id"], sco)

        ok, err, _ = cm.implement_change_request(wdb, req["id"], sco)
        assert not ok
        assert "version" in err.lower() or "updated" in err.lower()

    def test_add_director(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "director_add", "person_action": "add",
                   "person_snapshot": {"person_key": "dir_new", "full_name": "Alice Brown",
                                       "first_name": "Alice", "last_name": "Brown",
                                       "nationality": "GB", "date_of_birth": "1990-03-20",
                                       "is_pep": False},
                   "materiality": "tier1"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "New director", items, sco)
        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)
        cm.approve_change_request(wdb, req["id"], sco)
        ok, err, _ = cm.implement_change_request(wdb, req["id"], sco)
        assert ok, f"Failed: {err}"

        pkeys = [d["person_key"] for d in db.execute(
            "SELECT person_key FROM directors WHERE application_id = ?", (app_id,)).fetchall()]
        assert "dir_new" in pkeys

    def test_remove_director(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        items = [{"change_type": "director_remove", "person_action": "remove",
                   "person_snapshot": {"person_key": "dir1"}, "materiality": "tier1"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Dir resignation", items, sco)
        cm.submit_change_request(wdb, req["id"], sco)
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)
        cm.approve_change_request(wdb, req["id"], sco)
        ok, _, _ = cm.implement_change_request(wdb, req["id"], sco)
        assert ok

        dirs = db.execute("SELECT * FROM directors WHERE application_id = ? AND person_key = 'dir1'",
                          (app_id,)).fetchall()
        assert len(dirs) == 0

    def test_profile_snapshot(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        snap = cm.snapshot_entity_profile(wdb, app_id)
        assert snap["company_name"] == "Test Company Ltd"
        assert snap["country"] == "GB"
        assert len(snap["directors"]) == 1
        assert len(snap["ubos"]) == 1

    def test_profile_versions(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "sco"}

        assert len(cm.get_profile_versions(wdb, app_id)) == 0

        cm._create_profile_version(wdb, app_id, "r1", {}, {"v": 1}, user)
        wdb.commit()
        versions = cm.get_profile_versions(wdb, app_id)
        assert len(versions) == 1
        assert versions[0]["version_number"] == 1

    def test_attach_document(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "co"}

        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", _items, user)
        doc = cm.attach_document_to_request(wdb, req["id"], "cert.pdf", "supporting",
                                             "/tmp/cert.pdf", uploaded_by="u1")
        assert doc["doc_name"] == "cert.pdf"

        detail = cm.get_change_request_detail(wdb, req["id"])
        assert len(detail["documents"]) == 1

    def test_list_alerts(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "sco"}

        cm.create_change_alert(wdb, app_id, "director_change", "companies_house",
                               "A1", {}, user=user)
        cm.create_change_alert(wdb, app_id, "ubo_change", "open_corporates",
                               "A2", {}, user=user)

        alerts = cm.list_change_alerts(wdb, application_id=app_id)
        assert len(alerts) >= 2

    def test_list_requests(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "co"}

        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice", "R1", _items, user)
        cm.create_change_request(wdb, app_id, "portal_client", "portal", "R2", _items, user)

        reqs = cm.list_change_requests(wdb, application_id=app_id)
        assert len(reqs) >= 2

    def test_stats(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        user = {"sub": "u1", "name": "User", "role": "co"}

        cm.create_change_alert(wdb, app_id, "other", "backoffice", "T", {}, user=user)
        _items = [{"change_type": "other", "field_name": "note", "new_value": "test"}]
        cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice", "T", _items, user)

        stats = cm.get_change_management_stats(wdb)
        assert stats["alerts"]["total"] >= 1
        assert stats["requests"]["total"] >= 1

    def test_no_silent_overwrite(self, db):
        """Approved profile data must NOT change until explicit implementation."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)
        sco = {"sub": "sco1", "name": "SCO", "role": "sco"}

        orig = db.execute("SELECT company_name FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert orig["company_name"] == "Test Company Ltd"

        items = [{"change_type": "company_details", "field_name": "company_name",
                   "old_value": "Test Company Ltd", "new_value": "Changed"}]
        req = cm.create_change_request(wdb, app_id, "backoffice_manual", "backoffice",
                                        "Test", items, sco)

        # Unchanged after create
        assert db.execute("SELECT company_name FROM applications WHERE id = ?",
                          (app_id,)).fetchone()["company_name"] == "Test Company Ltd"

        # Unchanged after submit
        cm.submit_change_request(wdb, req["id"], sco)
        assert db.execute("SELECT company_name FROM applications WHERE id = ?",
                          (app_id,)).fetchone()["company_name"] == "Test Company Ltd"

        # Unchanged after approve
        cm.update_change_request_status(wdb, req["id"], "triage_in_progress", sco)
        cm.update_change_request_status(wdb, req["id"], "ready_for_review", sco)
        cm.update_change_request_status(wdb, req["id"], "approval_pending", sco)
        cm.approve_change_request(wdb, req["id"], sco)
        assert db.execute("SELECT company_name FROM applications WHERE id = ?",
                          (app_id,)).fetchone()["company_name"] == "Test Company Ltd"

        # Changes only after implementation
        ok, _, _ = cm.implement_change_request(wdb, req["id"], sco)
        assert ok
        assert db.execute("SELECT company_name FROM applications WHERE id = ?",
                          (app_id,)).fetchone()["company_name"] == "Changed"

    def test_unsafe_field_blocked(self, db):
        """Unsafe fields cannot be modified via _apply_field_change."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        with pytest.raises(ValueError, match="Unsupported/unsafe field"):
            cm._apply_field_change(wdb, app_id, "status", "rejected")
        assert db.execute("SELECT status FROM applications WHERE id = ?",
                          (app_id,)).fetchone()["status"] == "approved"

    def test_director_ownership_pct_blocked(self, db):
        """Directors table has no ownership_pct — update must be silently blocked."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        # Get the test director's person_key
        d = db.execute("SELECT person_key, full_name FROM directors WHERE application_id = ?",
                       (app_id,)).fetchone()
        assert d is not None

        # Attempt to update ownership_pct on a director — should be blocked
        cm._apply_person_change(
            wdb, app_id, "directors", "update",
            {"person_key": d["person_key"]},
            "ownership_pct", "25.0",
        )
        # Director should be unchanged — no SQL error
        d2 = db.execute("SELECT full_name FROM directors WHERE application_id = ? AND person_key = ?",
                        (app_id, d["person_key"])).fetchone()
        assert d2 is not None
        assert d2["full_name"] == d["full_name"]

    def test_ubo_ownership_pct_allowed(self, db):
        """UBOs table has ownership_pct — update must be allowed."""
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id, _ = _setup_test_data(db)

        u = db.execute("SELECT person_key FROM ubos WHERE application_id = ?",
                       (app_id,)).fetchone()
        assert u is not None

        cm._apply_person_change(
            wdb, app_id, "ubos", "update",
            {"person_key": u["person_key"]},
            "ownership_pct", "50.0",
        )
        wdb.commit()
        u2 = db.execute("SELECT ownership_pct FROM ubos WHERE application_id = ? AND person_key = ?",
                        (app_id, u["person_key"])).fetchone()
        assert float(u2["ownership_pct"]) == 50.0
