"""
Tests for PR-CM-LOCK-AND-AUTO-DRAFT-1.

Covers the service-layer primitives that lock approved profiles and stage
attempted edits as draft Change Requests instead of mutating live data:

- is_profile_locked
- diff_application_fields (material vs unchanged vs minor)
- find_open_draft_for_items (idempotency key)
- stage_locked_profile_edit (auto-draft + idempotent reuse + no live mutation)
"""

import secrets


def _get_cm():
    import change_management as cm
    return cm


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


def _setup_approved_app(raw_db):
    app_id = f"test-lock-{secrets.token_hex(4)}"
    client_id = f"test-cl-{secrets.token_hex(4)}"
    raw_db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"test-{secrets.token_hex(3)}@test.com", "hash", "Test Company"),
    )
    raw_db.execute(
        """INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"APP-{secrets.token_hex(4)}", client_id, "Original Co Ltd",
         "GB", "Financial Services", "Limited Company", "approved"),
    )
    raw_db.commit()
    return app_id


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

class TestProfileLocking:
    def test_approved_is_locked(self):
        cm = _get_cm()
        assert cm.is_profile_locked("approved") is True

    def test_draft_is_not_locked(self):
        cm = _get_cm()
        assert cm.is_profile_locked("draft") is False
        assert cm.is_profile_locked(None) is False

    def test_open_statuses_exclude_terminal(self):
        cm = _get_cm()
        for terminal in ("approved", "rejected", "implemented", "cancelled", "superseded"):
            assert terminal not in cm.OPEN_CHANGE_REQUEST_STATUSES


class TestDiffApplicationFields:
    def test_detects_material_change(self):
        cm = _get_cm()
        app = {"company_name": "Original Co Ltd", "country": "GB"}
        items = cm.diff_application_fields(app, {"country": "MT"})
        assert len(items) == 1
        assert items[0]["field_name"] == "country"
        assert items[0]["old_value"] == "GB"
        assert items[0]["new_value"] == "MT"
        assert items[0]["change_type"] == "address_change"

    def test_ignores_unchanged_value(self):
        cm = _get_cm()
        app = {"company_name": "Original Co Ltd"}
        assert cm.diff_application_fields(app, {"company_name": "Original Co Ltd"}) == []

    def test_none_and_empty_string_equal(self):
        cm = _get_cm()
        app = {"sector": None}
        assert cm.diff_application_fields(app, {"sector": ""}) == []

    def test_minor_fields_excluded(self):
        cm = _get_cm()
        app = {"website": "old.com"}
        assert cm.diff_application_fields(app, {"website": "new.com"}) == []


# ---------------------------------------------------------------------------
# DB-backed: staging behaviour
# ---------------------------------------------------------------------------

class TestStageLockedProfileEdit:
    def test_creates_draft_without_mutating_live_profile(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_approved_app(db)
        user = {"sub": "officer1", "name": "Officer One", "role": "co"}

        app_row = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        items = cm.diff_application_fields(app_row, {"country": "MT"})
        result = cm.stage_locked_profile_edit(wdb, app_row, items, user)

        # Structured response
        assert result["action"] == "change_request_drafted"
        assert result["request_id"].startswith("CR-")
        assert result["recommended_next_action"] == "complete_change_request"
        assert result["prefilled_items"][0]["new_value"] == "MT"

        # Live profile is UNCHANGED — staging only
        after = db.execute("SELECT country FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert after["country"] == "GB"

        # A draft CR exists with the item recorded
        cr = db.execute(
            "SELECT status FROM change_requests WHERE id = ?", (result["request_id"],)
        ).fetchone()
        assert cr["status"] == "draft"
        item = db.execute(
            "SELECT field_name, old_value, new_value FROM change_request_items WHERE request_id = ?",
            (result["request_id"],),
        ).fetchone()
        assert item["field_name"] == "country"
        assert item["old_value"] == "GB"
        assert item["new_value"] == "MT"

    def test_idempotent_repeat_edit_returns_existing_draft(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_approved_app(db)
        user = {"sub": "officer1", "name": "Officer One", "role": "co"}
        app_row = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        items = cm.diff_application_fields(app_row, {"country": "MT"})

        first = cm.stage_locked_profile_edit(wdb, app_row, items, user)
        second = cm.stage_locked_profile_edit(wdb, app_row, items, user)

        assert second["action"] == "change_request_exists"
        assert second["request_id"] == first["request_id"]
        assert second["recommended_next_action"] == "open_existing_change_request"

        # Only ONE change request was created for this application
        count = db.execute(
            "SELECT COUNT(*) AS c FROM change_requests WHERE application_id = ?", (app_id,)
        ).fetchone()
        assert count["c"] == 1

    def test_distinct_field_edit_creates_separate_draft(self, db):
        cm = _get_cm()
        wdb = _DBWrapper(db)
        app_id = _setup_approved_app(db)
        user = {"sub": "officer1", "name": "Officer One", "role": "co"}
        app_row = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())

        r1 = cm.stage_locked_profile_edit(
            wdb, app_row, cm.diff_application_fields(app_row, {"country": "MT"}), user
        )
        r2 = cm.stage_locked_profile_edit(
            wdb, app_row, cm.diff_application_fields(app_row, {"sector": "Crypto"}), user
        )
        assert r1["request_id"] != r2["request_id"]
        count = db.execute(
            "SELECT COUNT(*) AS c FROM change_requests WHERE application_id = ?", (app_id,)
        ).fetchone()
        assert count["c"] == 2
