"""Authorization boundary for synthetic fixture cleanup.

This module does not provide generic table wiping.  Callers must identify one
marked application and explicitly enumerate any regulated tables they need to
touch inside the yielded sanctioned context.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

from regulated_deletion import (
    FIXTURE_CLEANUP_CONFIRMATION,
    is_verified_isolated_test_database,
    sanctioned_delete_context,
)
from fixtures.registry import NEGATIVE_PATH_FIXTURES


class FixtureCleanupDenied(RuntimeError):
    pass


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _identity_matches(row, manifest, role="root") -> bool:
    data = row.get("prescreening_data")
    if not isinstance(data, dict):
        try:
            data = json.loads(data or "{}")
        except (TypeError, ValueError):
            data = {}
    expected = {
        "fixture": manifest["scenario_code"],
        "fixture_key": manifest["fixture_key"],
        "fixture_marker": manifest["marker"],
        "fixture_role": role,
        "source": "fixtures.seeder",
    }
    return all(data.get(key) == value for key, value in expected.items())


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
    root = db.execute(
        "SELECT id, ref, is_fixture, prescreening_data FROM applications WHERE ref=?",
        (manifest["synthetic_ref"],),
    ).fetchone()
    if not root or not _truthy(root.get("is_fixture")) or not _identity_matches(root, manifest):
        raise FixtureCleanupDenied(
            f"registered fixture identity is missing or mismatched: {fixture_key}"
        )
    app_id = root["id"]
    pair = None
    if manifest.get("paired_synthetic_ref"):
        pair = db.execute(
            "SELECT id, ref, is_fixture, prescreening_data FROM applications WHERE ref=?",
            (manifest["paired_synthetic_ref"],),
        ).fetchone()
        if not pair or not _truthy(pair.get("is_fixture")) or not _identity_matches(
            pair, manifest, "pair_b"
        ):
            raise FixtureCleanupDenied(
                f"registered paired fixture identity is missing or mismatched: {fixture_key}"
            )
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
                    remove(
                        table,
                        "request_id IN (SELECT id FROM rmi_requests WHERE application_id=? AND reason=?)",
                        (app_id, "FIX-SCEN17 pending information request"),
                    )
                elif table == "rmi_requests":
                    remove(table, "application_id=?", (app_id,))
                elif table == "compliance_memos":
                    remove(table, "application_id=? AND memo_data LIKE ?", (app_id, "%FIX_SCEN_13_COMPLIANCE_MEMO%"))
                elif table == "periodic_reviews":
                    remove(table, "application_id=? AND trigger_reason LIKE ?", (app_id, "FIX_SCEN%_ITEM36%"))
                elif table == "decision_records":
                    remove(
                        table,
                        "application_ref=? AND actor_user_id=? AND key_flags LIKE ?",
                        (manifest["synthetic_ref"], "fixture_seed", f"%{manifest['marker']}%"),
                    )
                elif table == "audit_log":
                    remove(table, "user_id='fixture_seed' AND detail LIKE ?", (f"%{manifest['scenario_code']}%",))
                elif table == "directors":
                    full_name = {
                        "missing-idv": "FIX-SCEN15 Unverified Director",
                        "outstanding-pep-review": "FIX-SCEN19 Synthetic PEP",
                    }.get(fixture_key)
                    if not full_name:
                        raise FixtureCleanupDenied(
                            f"cleanup has no director marker for {fixture_key}"
                        )
                    remove(table, "application_id=? AND full_name=?", (app_id, full_name))
                elif table == "applications":
                    applications = [root] + ([pair] if pair else [])
                    for fixture_app in applications:
                        remove(
                            table,
                            "id=? AND ref=? AND is_fixture=?",
                            (fixture_app["id"], fixture_app["ref"], True),
                        )
                elif table == "clients":
                    for suffix in ("a", "b"):
                        remove(
                            table,
                            "email=? AND company_name=?",
                            (
                                f"fix-scen22-{suffix}@fixture.invalid",
                                f"FIX-SCEN22 Client {suffix.upper()}",
                            ),
                        )
                else:
                    raise FixtureCleanupDenied(
                        f"cleanup table has no marker-scoped implementation: {table}"
                    )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return counts
