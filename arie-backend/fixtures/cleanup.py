"""Authorization boundary for synthetic fixture cleanup.

This module does not provide generic table wiping.  Callers must identify one
marked application and explicitly enumerate any regulated tables they need to
touch inside the yielded sanctioned context.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from regulated_deletion import (
    FIXTURE_CLEANUP_CONFIRMATION,
    is_verified_isolated_test_database,
    sanctioned_delete_context,
)
from fixtures.registry import APP_ID, NEGATIVE_PATH_FIXTURES


class FixtureCleanupDenied(RuntimeError):
    pass


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def fixture_cleanup_context(
    db,
    application_id: str,
    *,
    actor_id: str,
    confirmation: str,
    reason: str,
    allowed_tables,
):
    """Yield a sanctioned context after environment, DB and marker checks."""
    environment = (os.environ.get("ENVIRONMENT") or "development").strip().lower()
    if environment not in {"test", "testing", "staging"}:
        raise FixtureCleanupDenied("fixture cleanup is permitted only in testing or staging")
    if confirmation != FIXTURE_CLEANUP_CONFIRMATION:
        raise FixtureCleanupDenied("fixture cleanup confirmation is missing or invalid")
    if environment in {"test", "testing"} and not is_verified_isolated_test_database(
        getattr(db, "database_identity", None), getattr(db, "is_postgres", False)
    ):
        raise FixtureCleanupDenied("testing cleanup requires a verified isolated SQLite database")

    app = db.execute(
        "SELECT id, is_fixture FROM applications WHERE id=?",
        (application_id,),
    ).fetchone()
    if not app or not _truthy(app.get("is_fixture")):
        raise FixtureCleanupDenied("application is not explicitly marked as a fixture")

    with sanctioned_delete_context(
        "fixture_cleanup_nonprod",
        actor_id=actor_id,
        role="system",
        reason=reason,
        application_id=application_id,
        environment=environment,
        is_fixture=True,
        confirmed=True,
        allowed_tables=tuple(allowed_tables),
    ) as context:
        yield context


def cleanup_registered_fixture(
    db,
    fixture_key: str,
    *,
    actor_id: str = "fixture_cleanup",
    confirmation: str = FIXTURE_CLEANUP_CONFIRMATION,
):
    """Delete one Item 36 fixture through the sanctioned marker-scoped path."""
    manifest = NEGATIVE_PATH_FIXTURES.get(fixture_key)
    if not manifest:
        raise FixtureCleanupDenied(f"unknown registered fixture: {fixture_key}")
    app_id = APP_ID[manifest["scenario_code"]]
    pair_id = "f1xed0000000022b" if fixture_key == "similar-reference-cross-client" else None
    allowed = manifest["regulated_tables_written"]
    counts = {}

    def remove(table, where, params):
        row = db.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", params).fetchone()
        counts[table] = counts.get(table, 0) + int((row or {}).get("n") or 0)
        db.execute(f"DELETE FROM {table} WHERE {where}", params)

    try:
        with fixture_cleanup_context(
            db,
            app_id,
            actor_id=actor_id,
            confirmation=confirmation,
            reason=f"Item 36 cleanup for {fixture_key}",
            allowed_tables=allowed,
        ):
            for table in manifest["cleanup_order"]:
                if table == "rmi_request_items":
                    remove(table, "request_id=?", ("fix-scen17-rmi",))
                elif table == "rmi_requests":
                    remove(table, "application_id=?", (app_id,))
                elif table == "compliance_memos":
                    remove(table, "application_id=? AND memo_data LIKE ?", (app_id, "%FIX_SCEN_13_COMPLIANCE_MEMO%"))
                elif table == "periodic_reviews":
                    remove(table, "application_id=? AND trigger_reason LIKE ?", (app_id, "FIX_SCEN%_ITEM36%"))
                elif table == "decision_records":
                    remove(table, "id=?", ("fix-scen23-decision",))
                elif table == "audit_log":
                    remove(table, "user_id='fixture_seed' AND detail LIKE ?", (f"%{manifest['scenario_code']}%",))
                elif table == "directors":
                    director_id = {
                        "missing-idv": "fix-scen15-director",
                        "outstanding-pep-review": "fix-scen19-pep",
                    }.get(fixture_key)
                    if not director_id:
                        raise FixtureCleanupDenied(
                            f"cleanup has no director marker for {fixture_key}"
                        )
                    remove(table, "application_id=? AND id=?", (app_id, director_id))
                elif table == "applications":
                    ids = (app_id, pair_id) if pair_id else (app_id,)
                    for fixture_app_id in ids:
                        remove(table, "id=? AND is_fixture=?", (fixture_app_id, True))
                elif table == "clients":
                    for client_id in ("fix-scen22-client-a", "fix-scen22-client-b"):
                        remove(table, "id=?", (client_id,))
                else:
                    raise FixtureCleanupDenied(
                        f"cleanup table has no marker-scoped implementation: {table}"
                    )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return counts
