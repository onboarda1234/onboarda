"""Schema-drift detection (P12-4 second half / DCI-004) — WARN-ONLY.

Compares the live database's table/column NAMES against the checked-in
manifest ``schema_expected.json`` and reports drift. It never blocks: the
startup hook logs warnings and emits a metric, then returns — boot, requests
and workflows proceed exactly as before. (The P12-4 first half — migration
hard-stops, #711 — remains the enforcing control; this is the detection
control the register records as the open ◐ half.)

Scope (v1, deliberate): names only — tables and their columns. Type/constraint
drift and index inventory are out of scope. The comparison classifies:

- ``missing_tables`` / ``missing_columns`` — expected but absent in the live
  DB. This is the dangerous direction (code paths assume them) → WARNING.
- ``extra_tables`` / ``extra_columns`` — present but not in the manifest
  (e.g. a newer migration ran, or engine-specific artifacts) → reported at
  INFO, never warned.

The manifest is generated from a FRESH ``db.init_db()`` (the same call every
environment boots through), so table/column names are engine-agnostic. A CI
test regenerates it and fails when the checked-in copy is stale, keeping the
manifest in lockstep with schema changes; a PG-marked test proves name parity
on real PostgreSQL.

CLI:
    python schema_drift.py            # report drift of the configured DB
    python schema_drift.py --check    # same, exit 1 on missing_* drift (CI/dev)
    python schema_drift.py --write    # regenerate schema_expected.json from
                                      # the configured DB (use a FRESH init)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("arie.schema_drift")

MANIFEST_PATH = Path(__file__).resolve().parent / "schema_expected.json"

# Engine/bookkeeping artifacts excluded from comparison on both sides.
_IGNORED_TABLES = {
    "sqlite_sequence",          # SQLite AUTOINCREMENT bookkeeping
    "schema_version",           # migration bookkeeping (runner-owned timing)
    "schema_migrations",
}


def introspect_schema(db) -> dict:
    """Return {table_name: sorted([column names])} for the live database.

    Works on both engines the platform supports: PostgreSQL (information_schema,
    current schema) and SQLite (sqlite_master + PRAGMA table_info).
    """
    tables: dict = {}
    if getattr(db, "is_postgres", False):
        rows = db.execute(
            "SELECT c.table_name, c.column_name "
            "FROM information_schema.columns c "
            "JOIN information_schema.tables t "
            "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = current_schema() "
            "  AND t.table_type = 'BASE TABLE' "
            "ORDER BY c.table_name, c.column_name"
        ).fetchall()
        for r in rows:
            t = r["table_name"]
            if t in _IGNORED_TABLES:
                continue
            tables.setdefault(t, []).append(r["column_name"])
    else:
        names = [
            r["name"] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for t in names:
            if t in _IGNORED_TABLES:
                continue
            cols = [
                r["name"] for r in db.execute(f'PRAGMA table_info("{t}")').fetchall()
            ]
            tables[t] = cols
    return {t: sorted(cols) for t, cols in tables.items()}


def load_expected(path: Path = MANIFEST_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("tables", {})


def compare(expected: dict, live: dict) -> dict:
    """Names-only comparison. Missing = expected-but-absent (dangerous);
    extra = present-but-unlisted (informational)."""
    findings = {
        "missing_tables": sorted(set(expected) - set(live)),
        "extra_tables": sorted(set(live) - set(expected)),
        "missing_columns": {},
        "extra_columns": {},
    }
    for t in sorted(set(expected) & set(live)):
        exp_cols, live_cols = set(expected[t]), set(live[t])
        if exp_cols - live_cols:
            findings["missing_columns"][t] = sorted(exp_cols - live_cols)
        if live_cols - exp_cols:
            findings["extra_columns"][t] = sorted(live_cols - exp_cols)
    findings["has_missing"] = bool(
        findings["missing_tables"] or findings["missing_columns"])
    findings["has_any"] = bool(
        findings["has_missing"] or findings["extra_tables"]
        or findings["extra_columns"])
    return findings


def check_and_log(db) -> dict:
    """Startup hook: detect and REPORT drift. Never raises, never blocks.

    Returns the findings dict ({} on any internal failure) so callers/tests
    can inspect it, but no caller decision may depend on it — this is a
    detection control only (DCI-004).
    """
    try:
        expected = load_expected()
        live = introspect_schema(db)
        findings = compare(expected, live)

        if findings["has_missing"]:
            logger.warning(
                "SCHEMA DRIFT (DCI-004): expected schema objects are MISSING "
                "from the live database — missing_tables=%s missing_columns=%s. "
                "Code paths that assume them may fail; investigate before "
                "relying on affected workflows.",
                findings["missing_tables"], findings["missing_columns"],
            )
        if findings["extra_tables"] or findings["extra_columns"]:
            logger.info(
                "schema_drift: objects present but not in the manifest "
                "(newer migration or engine artifact) — extra_tables=%s "
                "extra_columns=%s",
                findings["extra_tables"], findings["extra_columns"],
            )
        if not findings["has_any"]:
            logger.info("schema_drift: live schema matches schema_expected.json "
                        "(%d tables)", len(expected))

        try:
            from observability import emit_cloudwatch_metric_log
            emit_cloudwatch_metric_log(
                "SchemaDriftMissingObjects",
                len(findings["missing_tables"]) + len(findings["missing_columns"]),
                unit="Count",
                service="backend",
            )
        except Exception:
            pass
        return findings
    except Exception:
        # Warn-only contract: detection must never take the platform down.
        logger.exception("schema_drift: detection failed (non-fatal)")
        return {}


def write_manifest(db, path: Path = MANIFEST_PATH) -> dict:
    live = introspect_schema(db)
    payload = {
        "_comment": (
            "Generated by schema_drift.py --write from a FRESH db.init_db(). "
            "Names only (tables/columns). CI regenerates and diffs this; "
            "update it in the same PR as any schema change."
        ),
        "tables": live,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return live


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when missing_* drift is found")
    parser.add_argument("--write", action="store_true",
                        help="regenerate schema_expected.json from the "
                             "configured database")
    args = parser.parse_args(argv)

    from db import get_db
    db = get_db()
    try:
        if args.write:
            live = write_manifest(db)
            print(f"Wrote {MANIFEST_PATH.name}: {len(live)} tables")
            return 0
        findings = compare(load_expected(), introspect_schema(db))
        print(json.dumps(findings, indent=2, sort_keys=True))
        if args.check and findings["has_missing"]:
            return 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
