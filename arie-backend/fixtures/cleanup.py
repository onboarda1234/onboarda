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
    audit_log_maintenance_window,
    is_verified_isolated_test_database,
    sanctioned_delete_context,
)
from fixtures.registry import NEGATIVE_PATH_FIXTURES
from fixtures.pilot_canonical import (
    DATASET_NAME,
    DATASET_VERSION,
    load_manifest,
    manifest_sha256,
)


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
                    # P10-7: audit_log carries append-only DB triggers on
                    # staging; this sanctioned fixture cleanup opens a
                    # transient window (commit happens after this block, so
                    # the marker row never survives it).
                    with audit_log_maintenance_window(
                        db, actor_id=actor_id or "fixture_cleanup",
                        reason=f"fixture cleanup {manifest['scenario_code']}",
                    ):
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


PILOT_CANONICAL_CLEANUP_TABLES = (
    "rmi_request_items",
    "rmi_requests",
    "application_corrections",
    "edd_findings",
    "compliance_memos",
    "edd_cases",
    "periodic_reviews",
    "screening_reviews",
    "monitoring_alerts",
    "decision_records",
    "audit_log",
    "documents",
    "intermediaries",
    "ubos",
    "directors",
    "applications",
)

PILOT_CANONICAL_REGULATED_CLEANUP_TABLES = (
    "rmi_request_items",
    "rmi_requests",
    "application_corrections",
    "edd_findings",
    "compliance_memos",
    "edd_cases",
    "periodic_reviews",
    "screening_reviews",
    "monitoring_alerts",
    "decision_records",
    "audit_log",
)


def _pilot_identity_matches(row, manifest_row) -> bool:
    data = row.get("prescreening_data")
    if not isinstance(data, dict):
        try:
            data = json.loads(data or "{}")
        except (TypeError, ValueError):
            data = {}
    expected = {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "dataset_hash": manifest_sha256(),
        "fixture": True,
        "synthetic": True,
        "non_production": True,
        "source": "fixtures.pilot_canonical_seeder",
        "scenario_reference": manifest_row["reference"],
        "scenario_slug": manifest_row["slug"],
    }
    return all(data.get(key) == value for key, value in expected.items())


def cleanup_pilot_canonical_dataset(
    db,
    *,
    actor_id: str,
    confirmation: str,
    reviewed_hash: str,
):
    """Remove only the exact reviewed Pilot Canonical Dataset from staging.

    The operation refuses production/development, non-fixtures, identity/hash
    drift and any unexpected record in the reserved namespace. Each root is
    removed inside the existing sanctioned regulated-deletion context.
    """
    environment = (os.environ.get("ENVIRONMENT") or "development").strip().lower()
    if environment != "staging":
        raise FixtureCleanupDenied("pilot canonical cleanup is permitted only in staging")
    if confirmation != FIXTURE_CLEANUP_CONFIRMATION:
        raise FixtureCleanupDenied("pilot canonical cleanup confirmation is missing or invalid")
    current_hash = manifest_sha256()
    if reviewed_hash != current_hash:
        raise FixtureCleanupDenied("pilot canonical reviewed manifest hash is missing or invalid")

    manifest_rows = {row["reference"]: row for row in load_manifest()["scenarios"]}
    roots = db.execute(
        "SELECT id, ref, is_fixture, prescreening_data FROM applications "
        "WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref"
    ).fetchall()
    if not roots:
        return {table: 0 for table in PILOT_CANONICAL_CLEANUP_TABLES}

    for root in roots:
        manifest_row = manifest_rows.get(root["ref"])
        if (
            not manifest_row
            or root["id"] != manifest_row["application_id"]
            or not _truthy(root.get("is_fixture"))
            or not _pilot_identity_matches(root, manifest_row)
        ):
            raise FixtureCleanupDenied(
                f"reserved pilot identity is missing or mismatched: {root['ref']}"
            )

    counts = {table: 0 for table in PILOT_CANONICAL_CLEANUP_TABLES}

    def remove(table, where, params):
        result = db.execute(
            f"SELECT count(*) AS n FROM {table} WHERE {where}", params
        ).fetchone()
        counts[table] += int((result or {}).get("n") or 0)
        db.execute(f"DELETE FROM {table} WHERE {where}", params)

    try:
        for root in roots:
            app_id = root["id"]
            reference = root["ref"]
            with fixture_cleanup_context(
                db,
                app_id,
                actor_id=actor_id,
                confirmation=confirmation,
                reason=f"Pilot Canonical Dataset cleanup for {reference}",
                allowed_tables=PILOT_CANONICAL_REGULATED_CLEANUP_TABLES,
            ):
                remove(
                    "rmi_request_items",
                    "request_id IN (SELECT id FROM rmi_requests WHERE application_id=?)",
                    (app_id,),
                )
                remove("rmi_requests", "application_id=?", (app_id,))
                remove("application_corrections", "application_id=?", (app_id,))
                remove(
                    "edd_findings",
                    "edd_case_id IN (SELECT id FROM edd_cases WHERE application_id=?)",
                    (app_id,),
                )
                remove("compliance_memos", "application_id=?", (app_id,))
                remove("edd_cases", "application_id=?", (app_id,))
                remove("periodic_reviews", "application_id=?", (app_id,))
                remove("screening_reviews", "application_id=?", (app_id,))
                remove("monitoring_alerts", "application_id=?", (app_id,))
                remove(
                    "decision_records",
                    "id=? AND application_ref=?",
                    (f"{app_id}:decision", reference),
                )
                # P10-7: audit_log carries append-only DB triggers on staging;
                # this staging-only sanctioned cleanup opens a transient
                # window (commit happens after the loop, so the marker row
                # never survives it).
                with audit_log_maintenance_window(
                    db, actor_id=actor_id or "fixture_cleanup",
                    reason=f"pilot canonical cleanup {reference}",
                ):
                    remove(
                        "audit_log",
                        "user_id='fixture_seed' AND action LIKE 'fixture.pilot_canonical_%' "
                        "AND detail LIKE ?",
                        (f"%{reference}%",),
                    )
                    if reference == roots[-1]["ref"]:
                        remove(
                            "audit_log",
                            "user_id='fixture_seed' AND action='fixture.pilot_canonical_apply_complete' "
                            "AND target='dataset:pilot-canonical-v1' AND detail LIKE ?",
                            (
                                f"Applied % canonical scenarios at manifest {current_hash}",
                            ),
                        )
                for table in ("documents", "intermediaries", "ubos", "directors"):
                    remove(table, "application_id=?", (app_id,))
                remove(
                    "applications",
                    "id=? AND ref=? AND is_fixture=?",
                    (app_id, reference, True),
                )

        residue = db.execute(
            "SELECT count(*) AS n FROM applications WHERE ref LIKE 'RM-PILOT-%'"
        ).fetchone()
        if int((residue or {}).get("n") or 0):
            raise FixtureCleanupDenied("pilot canonical cleanup left application residue")
        audit_residue = db.execute(
            "SELECT count(*) AS n FROM audit_log "
            "WHERE user_id='fixture_seed' AND action LIKE 'fixture.pilot_canonical_%'"
        ).fetchone()
        if int((audit_residue or {}).get("n") or 0):
            raise FixtureCleanupDenied("pilot canonical cleanup left audit residue")
        db.commit()
    except Exception:
        db.rollback()
        raise

    return counts
