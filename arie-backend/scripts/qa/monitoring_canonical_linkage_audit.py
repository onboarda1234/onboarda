#!/usr/bin/env python3
"""Read-only canonical linkage inventory for Monitoring Alerts.

The collector bypasses application startup and schema initialisation.  It uses
one PostgreSQL REPEATABLE READ, READ ONLY transaction, permits SELECT/SHOW
statements only, builds the canonical linkage plan twice, verifies source
fingerprints, and explicitly rolls back before writing sanitised artifacts.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from environment import (  # noqa: E402
    FeatureFlags,
    MONITORING_FEATURE_FLAGS,
    get_monitoring_feature_state,
)
from monitoring_alert_linkage import (  # noqa: E402
    CONTRACT_VERSION,
    build_linkage_plan,
    plan_without_timestamp,
)


AUDIT_ID = "PR-MON-CANONICAL-LINKAGE-1"
ALLOWED_SQL_PREFIXES = ("select", "show", "with")
FORBIDDEN_SQL_TOKENS = frozenset(
    {
        "alter",
        "call",
        "commit",
        "copy",
        "create",
        "delete",
        "drop",
        "grant",
        "insert",
        "merge",
        "revoke",
        "truncate",
        "update",
    }
)

SOURCE_FINGERPRINT_SQL = """
WITH fingerprints AS (
    SELECT 'monitoring_alerts'::text AS relation, COUNT(*)::bigint AS row_count,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), '')) AS digest
      FROM monitoring_alerts t
    UNION ALL
    SELECT 'applications', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM applications t
    UNION ALL
    SELECT 'clients', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM clients t
    UNION ALL
    SELECT 'users', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM users t
    UNION ALL
    SELECT 'documents', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM documents t
    UNION ALL
    SELECT 'agent_executions', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM agent_executions t
    UNION ALL
    SELECT 'directors', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM directors t
    UNION ALL
    SELECT 'ubos', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM ubos t
    UNION ALL
    SELECT 'intermediaries', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM intermediaries t
    UNION ALL
    SELECT 'application_enhanced_requirements', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM application_enhanced_requirements t
    UNION ALL
    SELECT 'monitoring_alert_evidence', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM monitoring_alert_evidence t
    UNION ALL
    SELECT 'screening_reviews', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM screening_reviews t
    UNION ALL
    SELECT 'screening_hit_dispositions', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM screening_hit_dispositions t
    UNION ALL
    SELECT 'screening_reports_normalized', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM screening_reports_normalized t
    UNION ALL
    SELECT 'screening_owner_audit', COUNT(*)::bigint,
           MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''))
      FROM audit_log t
     WHERE t.action IN ('Screening Review', 'Screening Hit Disposition')
)
SELECT relation, row_count, digest FROM fingerprints ORDER BY relation
"""


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _markdown_cell(value: Any) -> str:
    return (
        str(value if value not in (None, "") else "")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def assert_select_only(sql: str) -> None:
    normalised = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL).strip()
    first = normalised.split(None, 1)[0].lower() if normalised else ""
    if first not in ALLOWED_SQL_PREFIXES:
        raise ValueError("Canonical linkage audit permits SELECT/WITH/SHOW only.")
    tokens = set(re.findall(r"[a-z_]+", normalised.lower()))
    forbidden = sorted(tokens & FORBIDDEN_SQL_TOKENS)
    if forbidden:
        raise ValueError(
            "Canonical linkage audit SQL contains forbidden statement token(s): "
            + ", ".join(forbidden)
        )


class _CursorResult:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        try:
            return self._cursor.fetchone()
        finally:
            self._cursor.close()

    def fetchall(self) -> list[Any]:
        try:
            return list(self._cursor.fetchall())
        finally:
            self._cursor.close()


class ReadOnlyPostgresAdapter:
    """Small DB adapter for the resolver with a hard SELECT-only boundary."""

    is_postgres = True

    def __init__(self, connection: Any, cursor_factory: Any) -> None:
        self._connection = connection
        self._cursor_factory = cursor_factory
        self._monitoring_linkage_snapshot_cache: dict[tuple[str, str, str], list[Any]] = {}

    def execute(self, sql: str, params: tuple = ()) -> _CursorResult:
        assert_select_only(sql)
        cursor = self._connection.cursor(cursor_factory=self._cursor_factory)
        cursor.execute(sql.replace("?", "%s"), tuple(params))
        return _CursorResult(cursor)

    def clear_snapshot_cache(self) -> None:
        """Force a fresh resolver pass inside the same repeatable-read snapshot."""
        self._monitoring_linkage_snapshot_cache.clear()


def _fingerprints(db: ReadOnlyPostgresAdapter) -> list[dict[str, Any]]:
    rows = db.execute(SOURCE_FINGERPRINT_SQL).fetchall()
    return [
        {
            "relation": str(row["relation"]),
            "row_count": int(row["row_count"]),
            "digest": str(row["digest"]),
        }
        for row in rows
    ]


def _governed_flags(environment_name: str) -> dict[str, Any]:
    state = get_monitoring_feature_state(FeatureFlags(environment_name))
    expected = set(MONITORING_FEATURE_FLAGS)
    if set(state) != expected:
        raise RuntimeError("Governed Monitoring feature inventory is incomplete.")
    if any(state.values()):
        raise RuntimeError("A governed Monitoring feature flag is ON; audit refused.")
    return {
        "features": {name: "OFF" for name in MONITORING_FEATURE_FLAGS},
        "all_monitoring_flags_off": True,
        "activation_controls": False,
        "evaluation_source": "collector_environment_with_staging_defaults",
        "deployed_runtime_observed": False,
    }


def collect_read_only_snapshot(
    connection: Any,
    cursor_factory: Any,
    *,
    environment_name: str,
    deployed_sha: str | None,
) -> dict[str, Any]:
    connection.set_session(
        readonly=True,
        autocommit=False,
        isolation_level="REPEATABLE READ",
    )
    db = ReadOnlyPostgresAdapter(connection, cursor_factory)
    transaction_read_only = str(
        db.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"]
    ).lower()
    transaction_isolation = str(
        db.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
    ).lower()
    if transaction_read_only != "on" or transaction_isolation != "repeatable read":
        raise RuntimeError("Database did not enter the required read-only snapshot mode.")

    before = _fingerprints(db)
    first = build_linkage_plan(db)
    db.clear_snapshot_cache()
    second = build_linkage_plan(db)
    after = _fingerprints(db)
    if before != after:
        raise RuntimeError("Source fingerprint drifted during the read-only audit.")
    if plan_without_timestamp(first) != plan_without_timestamp(second):
        raise RuntimeError("Canonical linkage plan was not repeatable.")

    flags = _governed_flags(environment_name)
    return {
        "audit_id": AUDIT_ID,
        "contract_version": CONTRACT_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": environment_name,
        "deployed_sha": deployed_sha or None,
        "database_safety": {
            "transaction_read_only": transaction_read_only,
            "transaction_isolation": transaction_isolation,
            "completion": "ROLLBACK",
            "commit_path": False,
            "source_fingerprints_stable": True,
        },
        "feature_governance": flags,
        "source_fingerprints": before,
        "plan": first,
        "mutation_gate_required": False,
        "no_monitoring_or_downstream_data_modified": True,
    }


def _markdown_table(snapshot: Mapping[str, Any]) -> str:
    plan = snapshot["plan"]
    counts = plan["counts"]
    lines = [
        "# PR-MON-CANONICAL-LINKAGE-1 — Linkage Audit Report",
        "",
        f"- Contract: `{snapshot['contract_version']}`",
        f"- Environment: `{snapshot['environment']}`",
        f"- Deployed SHA: `{snapshot.get('deployed_sha') or 'not recorded'}`",
        f"- Plan fingerprint: `{plan['fingerprint']}`",
        "- Database transaction: `READ ONLY`, `REPEATABLE READ`, completed by `ROLLBACK`",
        "- Collector-side governed-flag evaluation: all four `OFF` "
        "(not deployed-runtime evidence)",
        "- Apply support: `false`",
        "- Data changes planned: `0`",
        "",
        "## Statistics",
        "",
        "| Metric | Count |",
        "|---|---:|",
    ]
    for key in sorted(counts):
        lines.append(f"| {key.replace('_', ' ').title()} | {counts[key]} |")
    lines.extend(
        [
            "",
            "## Alert inventory",
            "",
            "| Alert | Stored type | Scope | Classification | Review | Duplicate | Reasons |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for row in plan["rows"]:
        reasons = _markdown_cell(", ".join(row.get("reasons") or []) or "—")
        lines.append(
            "| {alert_id} | `{stored_alert_type}` | {scope_status} | "
            "{linkage_classification} | {review_disposition} | {duplicate} | {reasons} |".format(
                alert_id=_markdown_cell(row["alert_id"]),
                stored_alert_type=_markdown_cell(row.get("stored_alert_type")),
                scope_status=_markdown_cell(row.get("scope_status")),
                linkage_classification=_markdown_cell(
                    row.get("linkage_classification")
                ),
                review_disposition=_markdown_cell(row.get("review_disposition")),
                duplicate="yes" if row.get("duplicate_linkage") else "no",
                reasons=reasons,
            )
        )
    lines.extend(
        [
            "",
            "## Safety conclusion",
            "",
            "No Monitoring Alert or downstream record was modified. The dry-run "
            "contract has no apply path. Rows that cannot be proven by exact stable "
            "identifiers remain unchanged and require manual review.",
            "",
        ]
    )
    return "\n".join(lines)


def _dry_run_markdown(snapshot: Mapping[str, Any]) -> str:
    plan = snapshot["plan"]
    lines = [
            "# PR-MON-CANONICAL-LINKAGE-1 — Dry-Run Report",
            "",
            f"- Contract version: `{snapshot['contract_version']}`",
            f"- Deterministic fingerprint: `{plan['fingerprint']}`",
            f"- Total alerts: `{plan['counts']['total_alerts']}`",
            f"- Correctly linked: `{plan['counts']['linked_correctly']}`",
            f"- Manual review required: `{plan['counts']['manual_review_required']}`",
            f"- Duplicate linkage groups: `{plan['counts']['duplicate_linkage_groups']}`",
            "- Safely backfillable: `0`",
            "- Apply supported: `false`",
            "- Data changes planned: `0`",
            "- Founder staging mutation gate: `not triggered`",
            "",
            "Every proposed linkage field in the JSON inventory is a read-time "
            "projection. No linkage field is written back to Monitoring Alerts.",
            "",
            "## Exact read-only projections",
            "",
            "| Alert | Linkage type | Proposed linkage fields |",
            "|---:|---|---|",
        ]
    for row in plan["rows"]:
        proposed = row.get("proposed_linkage")
        if not proposed:
            continue
        compact = json.dumps(
            proposed,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
        lines.append(
            f"| {_markdown_cell(row.get('alert_id'))} | "
            f"{_markdown_cell(row.get('linkage_type'))} | "
            f"`{_markdown_cell(compact)}` |"
        )
    lines.extend(
        [
            "",
            "Rows without a proposed linkage remain unchanged. Supported rows "
            "in that group require manual review; unsupported rows are out of scope.",
            "",
        ]
    )
    return "\n".join(lines)


def write_artifacts(snapshot: Mapping[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = output_dir / "canonical_linkage_inventory.json"
    report_path = output_dir / "LINKAGE_AUDIT_REPORT.md"
    dry_run_path = output_dir / "LINKAGE_DRY_RUN_REPORT.md"
    inventory_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_markdown_table(snapshot), encoding="utf-8")
    dry_run_path.write_text(_dry_run_markdown(snapshot), encoding="utf-8")
    return [inventory_path, report_path, dry_run_path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--environment", default=os.getenv("ENVIRONMENT", "staging"))
    parser.add_argument("--deployed-sha", default=os.getenv("GIT_SHA", ""))
    args = parser.parse_args(argv)
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the read-only audit.")

    import psycopg2
    from psycopg2.extras import RealDictCursor

    connection = psycopg2.connect(database_url)
    try:
        snapshot = collect_read_only_snapshot(
            connection,
            RealDictCursor,
            environment_name=args.environment,
            deployed_sha=args.deployed_sha,
        )
    finally:
        connection.rollback()
        connection.close()

    paths = write_artifacts(snapshot, args.output_dir)
    summary = {
        "audit_id": snapshot["audit_id"],
        "counts": snapshot["plan"]["counts"],
        "fingerprint": snapshot["plan"]["fingerprint"],
        "transaction_read_only": snapshot["database_safety"]["transaction_read_only"],
        "completion": snapshot["database_safety"]["completion"],
        "outputs": [str(path) for path in paths],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
