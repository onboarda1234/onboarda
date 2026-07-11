import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_append_audit_log_backward_compatible_and_metadata_enriched(temp_db):
    import db as dbmod
    from observability import clear_request_id, set_request_id

    suffix = uuid.uuid4().hex[:8]
    legacy_action = f"legacy.compat.{suffix}"
    explicit_action = f"metadata.explicit.{suffix}"
    context_action = f"metadata.context.{suffix}"
    application_id = f"app-727-{suffix}"

    conn = dbmod.get_db()
    try:
        dbmod.append_audit_log(conn, action=legacy_action, user_id="u-1", commit=True)
        legacy = conn.execute(
            "SELECT application_id, request_id FROM audit_log WHERE action = ?",
            (legacy_action,),
        ).fetchone()
        assert legacy["application_id"] is None
        assert legacy["request_id"] is None

        set_request_id("req-app727")
        dbmod.append_audit_log(
            conn,
            action=explicit_action,
            user_id="u-2",
            application_id=application_id,
            request_id="req-explicit",
            commit=True,
        )
        explicit = conn.execute(
            "SELECT application_id, request_id FROM audit_log WHERE action = ?",
            (explicit_action,),
        ).fetchone()
        assert explicit["application_id"] == application_id
        assert explicit["request_id"] == "req-explicit"

        dbmod.append_audit_log(
            conn,
            action=context_action,
            user_id="u-3",
            application_id=application_id,
            commit=True,
        )
        contextual = conn.execute(
            "SELECT application_id, request_id FROM audit_log WHERE action = ?",
            (context_action,),
        ).fetchone()
        assert contextual["application_id"] == application_id
        assert contextual["request_id"] == "req-app727"
    finally:
        clear_request_id()
        conn.close()


def test_append_audit_log_does_not_resolve_application_id_with_fresh_connection(
    temp_db,
    monkeypatch,
):
    import db as dbmod

    suffix = uuid.uuid4().hex[:8]
    application_id = f"app-uncommitted-{suffix}"
    application_ref = f"APP-UNCOMMITTED-{suffix}"
    action = f"same.transaction.{suffix}"

    conn = dbmod.get_db()
    def fail_get_db(*_args, **_kwargs):
        raise AssertionError("append_audit_log must not open a fresh DB connection")

    monkeypatch.setattr(dbmod, "get_db", fail_get_db)
    try:
        conn.execute(
            "INSERT INTO applications (id, ref, company_name, status) VALUES (?, ?, ?, ?)",
            (application_id, application_ref, "Uncommitted App Ltd", "draft"),
        )
        dbmod.append_audit_log(
            conn,
            action=action,
            target=f"application:{application_ref}",
            application_id=application_id,
            commit=False,
        )
        row = conn.execute(
            "SELECT application_id FROM audit_log WHERE action = ?",
            (action,),
        ).fetchone()
        assert row["application_id"] == application_id
        conn.rollback()
    finally:
        conn.close()


def test_base_handler_application_resolver_uses_passed_db_handle(temp_db, monkeypatch):
    import base_handler
    import db as dbmod

    suffix = uuid.uuid4().hex[:8]
    application_id = f"app-resolver-{suffix}"
    application_ref = f"APP-RESOLVER-{suffix}"
    review_id = int(suffix, 16)

    def fail_get_db(*_args, **_kwargs):
        raise AssertionError("resolver must use the caller-provided DB handle")

    monkeypatch.setattr(base_handler, "get_db", fail_get_db)

    conn = dbmod.get_db()
    try:
        conn.execute(
            "INSERT INTO applications (id, ref, company_name, status) VALUES (?, ?, ?, ?)",
            (application_id, application_ref, "Resolver App Ltd", "draft"),
        )
        conn.execute(
            "INSERT INTO periodic_reviews (id, application_id) VALUES (?, ?)",
            (review_id, application_id),
        )

        assert base_handler._audit_log_has_application_id(conn) is True
        assert (
            base_handler._resolve_audit_application_id(
                conn,
                f"application:{application_ref}",
            )
            == application_id
        )
        assert (
            base_handler._resolve_audit_application_id(
                conn,
                f"periodic_review:{review_id}",
            )
            == application_id
        )
        conn.rollback()
    finally:
        conn.close()


def test_hash_payload_and_hash_version_exclude_metadata(temp_db):
    import db as dbmod

    suffix = uuid.uuid4().hex[:8]
    action = f"metadata.hash.{suffix}"
    application_id = f"app-727-{suffix}"

    payload = dbmod._audit_log_hash_payload(
        {
            "user_id": "u",
            "action": action,
            "target": "application:APP-727",
            "application_id": application_id,
            "request_id": "req-app727",
            "timestamp": "2026-07-11T00:00:00Z",
        }
    )

    assert "application_id" not in payload
    assert "request_id" not in payload
    assert payload["hash_version"] == 1

    conn = dbmod.get_db()
    try:
        stored = dbmod.append_audit_log(
            conn,
            action=action,
            user_id="u",
            target="application:APP-727",
            application_id=application_id,
            request_id="req-app727",
            commit=True,
        )
        row = dict(
            conn.execute(
                "SELECT user_id, user_name, user_role, action, target, detail, "
                "ip_address, timestamp, before_state, after_state, previous_hash, "
                "entry_hash, application_id, request_id "
                "FROM audit_log WHERE action = ?",
                (action,),
            ).fetchone()
        )
        assert row["application_id"] == application_id
        assert row["request_id"] == "req-app727"
        assert dbmod._compute_audit_log_entry_hash(row) == stored == row["entry_hash"]
        assert dbmod.verify_audit_log_chain(conn)["verified"] is True
    finally:
        conn.close()
