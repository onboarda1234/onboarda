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
