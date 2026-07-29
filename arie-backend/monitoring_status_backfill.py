"""Controlled Monitoring Alert status reconciliation.

This module is an operator-only primitive for PR-MON-M1-STATUS-BACKFILL-1.
It is deliberately not imported by the API, startup, scheduler, or workers.

Safety properties:

* dry-run planning is deterministic and read-only;
* only exact manifest row identities and exact source statuses are eligible;
* PostgreSQL apply uses one transaction, a table writer lock, row locks, and
  the canonical hash-chained audit writer;
* any drift, missing audit infrastructure, update-count mismatch, or audit
  failure rolls the whole operation back;
* rollback is limited to the recorded migration rows and refuses if a later
  officer/workflow mutation is visible.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from environment import MONITORING_FEATURE_FLAGS
from monitoring_status import CANONICAL_ALERT_STATUSES, EXTENDED_ALERT_STATUSES


MIGRATION_ACTION = "monitoring.alert.status_backfilled"
ROLLBACK_ACTION = "monitoring.alert.status_backfill_rolled_back"
APPROVAL_PHRASE = "APPROVE STAGING STATUS BACKFILL"
APPROVED_MANIFEST_VERSION = "1.0.0"
# SHA-256 of canonical JSON (sorted keys, compact separators), not raw file
# whitespace. Updated only through review when the approved manifest changes.
APPROVED_MANIFEST_SHA256 = (
    "4764b491fff8afe03bf1afa26f3c6a874b34645b0067b0ab72bb2913e243ab16"
)
REQUIRED_AUDIT_COLUMNS = frozenset(
    {
        "id",
        "timestamp",
        "user_id",
        "user_name",
        "user_role",
        "action",
        "target",
        "detail",
        "ip_address",
        "before_state",
        "after_state",
        "previous_hash",
        "entry_hash",
        "application_id",
        "request_id",
    }
)
GOVERNED_FLAGS = tuple(MONITORING_FEATURE_FLAGS)


class ReconciliationError(RuntimeError):
    """Fail-closed reconciliation refusal with structured mismatch evidence."""

    def __init__(self, message: str, *, plan: Optional[dict[str, Any]] = None):
        self.plan = plan
        self.committed = False
        self.changed_rows: list[dict[str, Any]] = []
        self.audit_entries: list[dict[str, Any]] = []
        super().__init__(message)


class PostCommitReconciliationError(ReconciliationError):
    """A durable commit succeeded, but fresh-snapshot verification did not."""

    def __init__(
        self,
        message: str,
        *,
        plan: Optional[dict[str, Any]] = None,
        changed_rows: Optional[list[dict[str, Any]]] = None,
        audit_entries: Optional[list[dict[str, Any]]] = None,
    ):
        super().__init__(message, plan=plan)
        self.committed = True
        self.changed_rows = list(changed_rows or ())
        self.audit_entries = list(audit_entries or ())


def reconciliation_error_payload(exc: ReconciliationError) -> dict[str, Any]:
    """Return truthful, structured operator evidence for a reconciliation error."""
    return {
        "error": str(exc),
        "failed_closed": not exc.committed,
        "committed": exc.committed,
        "changed_rows": exc.changed_rows,
        "audit_entries": exc.audit_entries,
        "plan": exc.plan,
    }


def manifest_path() -> Path:
    return (
        Path(__file__).resolve().parent
        / "config"
        / "monitoring_status_backfill"
        / "PR-MON-M1-STATUS-BACKFILL-1.v1.json"
    )


def load_manifest(path: Optional[str | Path] = None) -> dict[str, Any]:
    selected = Path(path) if path else manifest_path()
    value = json.loads(selected.read_text(encoding="utf-8"))
    if value.get("migration_id") != "PR-MON-M1-STATUS-BACKFILL-1":
        raise ReconciliationError("unexpected or missing migration_id")
    if not value.get("manifest_version"):
        raise ReconciliationError("mapping manifest has no version")
    validate_approved_manifest(value)
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """Reject fuzzy, ambiguous, broad, or internally inconsistent mappings."""
    automatic = list(manifest.get("automatic_mappings") or ())
    if not automatic:
        raise ReconciliationError("mapping manifest has no automatic mappings")

    seen_ids: set[int] = set()
    for mapping in automatic:
        source = mapping.get("source_status")
        target = mapping.get("target_status")
        if mapping.get("mode") != "automatic" or not source or not target:
            raise ReconciliationError("automatic mapping requires exact source/target values")
        if source == target:
            raise ReconciliationError("automatic mutation mapping cannot be a no-op")
        if source not in set(EXTENDED_ALERT_STATUSES):
            raise ReconciliationError(
                f"automatic source status {source!r} is outside the exact audited vocabulary"
            )
        if target not in set(CANONICAL_ALERT_STATUSES):
            raise ReconciliationError(
                f"automatic target status {target!r} is not canonical"
            )
        if mapping.get("rollback_target") != source:
            raise ReconciliationError(
                "rollback target must equal the exact audited source status"
            )
        allowed_types = set(mapping.get("allowed_alert_types") or ())
        if not allowed_types:
            raise ReconciliationError(
                "automatic mapping requires an explicit alert-type allowlist"
            )
        rows = list(mapping.get("rows") or ())
        if mapping.get("expected_count") != len(rows):
            raise ReconciliationError("mapping expected_count does not equal exact row manifest")
        if not rows:
            raise ReconciliationError("automatic mapping must enumerate exact rows")
        for row in rows:
            required = {
                "id",
                "application_id",
                "application_ref",
                "alert_type",
                "severity",
                "effective_updated_at",
                "source_reference",
                "officer_action",
            }
            missing = sorted(required - set(row))
            if missing:
                raise ReconciliationError(
                    f"automatic row is missing exact preconditions: {missing}"
                )
            if row["alert_type"] not in allowed_types:
                raise ReconciliationError(
                    f"automatic row alert type {row['alert_type']!r} is not allowlisted"
                )
            row_id = int(row["id"])
            if row_id in seen_ids:
                raise ReconciliationError(f"duplicate manifest row id {row_id}")
            seen_ids.add(row_id)

    other_rows = list(manifest.get("already_canonical") or ()) + list(
        manifest.get("manual_review") or ()
    )
    for row in other_rows:
        row_id = int(row["id"])
        if row_id in seen_ids:
            raise ReconciliationError(f"row {row_id} appears in multiple dispositions")
        seen_ids.add(row_id)

    expected_total = int(manifest["baseline"]["expected_alert_count"])
    if len(seen_ids) != expected_total:
        raise ReconciliationError(
            f"manifest enumerates {len(seen_ids)} rows, expected {expected_total}"
        )


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    return stable_hash(manifest)


def validate_approved_manifest(manifest: Mapping[str, Any]) -> None:
    """Bind every mutation path to the reviewed v1 manifest bytes."""
    validate_manifest(manifest)
    if manifest.get("manifest_version") != APPROVED_MANIFEST_VERSION:
        raise ReconciliationError(
            f"only approved manifest version {APPROVED_MANIFEST_VERSION} may execute"
        )
    observed = manifest_digest(manifest)
    if observed != APPROVED_MANIFEST_SHA256:
        raise ReconciliationError(
            "mapping manifest digest does not match the approved v1 manifest"
        )


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _redacted_value(value: Any) -> dict[str, Any]:
    """Deterministic drift evidence without echoing raw regulated values."""
    return {
        "present": value not in (None, ""),
        "sha256": stable_hash(value),
    }


def _validated_sha256(value: Any) -> Optional[str]:
    """Accept only an exact lowercase SHA-256 hex digest from stored evidence."""
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _safe_hash_observation(value: Any) -> str:
    """Expose a valid digest or a deterministic marker, never malformed raw text."""
    validated = _validated_sha256(value)
    return validated or f"invalid:{stable_hash(value)[:16]}"


def _timestamp_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ")
    return str(value).replace("T", " ")


def _effective_updated_at(row: Mapping[str, Any]) -> Optional[str]:
    values = [
        _timestamp_text(row.get(key))
        for key in (
            "created_at",
            "reviewed_at",
            "triaged_at",
            "assigned_at",
            "resolved_at",
            "discovered_at",
        )
    ]
    populated = [value for value in values if value]
    return max(populated) if populated else None


def _rows_by_id(manifest: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for mapping in manifest["automatic_mappings"]:
        for row in mapping["rows"]:
            item = dict(row)
            item["source_status"] = mapping["source_status"]
            item["target_status"] = mapping["target_status"]
            item["mapping_reason"] = mapping["reason"]
            item["rollback_target"] = mapping["rollback_target"]
            item["disposition"] = "automatic"
            result[int(item["id"])] = item
    for row in manifest["already_canonical"]:
        item = dict(row)
        item["source_status"] = row["status"]
        item["target_status"] = row["future_status"]
        item["disposition"] = "already_canonical"
        result[int(item["id"])] = item
    for row in manifest["manual_review"]:
        item = dict(row)
        item["source_status"] = row["status"]
        item["target_status"] = row.get("future_status")
        item["disposition"] = "manual_review"
        result[int(item["id"])] = item
    return result


def _fetch_alerts(db: Any, *, for_update: bool = False) -> list[dict[str, Any]]:
    suffix = (
        " FOR UPDATE OF ma"
        if for_update and getattr(db, "is_postgres", False)
        else ""
    )
    return db.execute(
        """
        SELECT ma.id, ma.application_id, a.ref AS application_ref,
               ma.alert_type, ma.severity, ma.status, ma.provider,
               ma.case_identifier, ma.source_reference, ma.officer_action,
               ma.created_at, ma.reviewed_at, ma.triaged_at, ma.assigned_at,
               ma.resolved_at, ma.discovered_at
          FROM monitoring_alerts ma
     LEFT JOIN applications a ON a.id = ma.application_id
         ORDER BY ma.id
        """
        + suffix
    ).fetchall()


def _audit_columns(db: Any) -> set[str]:
    if getattr(db, "is_postgres", False):
        rows = db.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'audit_log'
            """
        ).fetchall()
        return {str(row["column_name"]) for row in rows}
    rows = db.execute("PRAGMA table_info(audit_log)").fetchall()
    return {str(row["name"]) for row in rows}


def _full_row_md5(
    db: Any,
    manifest: Mapping[str, Any],
    *,
    normalize_applied: bool = False,
) -> Optional[str]:
    if not getattr(db, "is_postgres", False):
        return None
    if not normalize_applied:
        row = db.execute(
            """
            SELECT MD5(COALESCE(
                STRING_AGG(TO_JSONB(t)::text, '' ORDER BY t.id::text), ''
            )) AS digest
              FROM monitoring_alerts t
            """
        ).fetchone()
        return row["digest"]
    cases = []
    params: list[Any] = []
    for mapping in manifest["automatic_mappings"]:
        for expected in mapping["rows"]:
            cases.append("WHEN t.id = ? AND t.status = ? THEN ?")
            params.extend(
                (
                    int(expected["id"]),
                    mapping["target_status"],
                    mapping["source_status"],
                )
            )
    row = db.execute(
        f"""
        SELECT MD5(COALESCE(
            STRING_AGG(
                (
                    TO_JSONB(t) ||
                    JSONB_BUILD_OBJECT(
                        'status',
                        CASE
                            {' '.join(cases)}
                            ELSE t.status
                        END
                    )
                )::text,
                '' ORDER BY t.id::text
            ),
            ''
        )) AS digest
          FROM monitoring_alerts t
        """,
        tuple(params),
    ).fetchone()
    return row["digest"]


def _migration_audits(
    db: Any, manifest: Mapping[str, Any], row_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    result = {row_id: [] for row_id in row_ids}
    if not row_ids:
        return result
    placeholders = ",".join("?" for _ in row_ids)
    targets = [f"monitoring_alert:{row_id}" for row_id in row_ids]
    rows = db.execute(
        f"""
        SELECT id, timestamp, action, target, detail, before_state, after_state,
               entry_hash
          FROM audit_log
         WHERE target IN ({placeholders})
           AND action IN (?, ?)
         ORDER BY id
        """,
        tuple(targets) + (MIGRATION_ACTION, ROLLBACK_ACTION),
    ).fetchall()
    migration_id = manifest["migration_id"]
    version = manifest["manifest_version"]
    for row in rows:
        try:
            detail = json.loads(row.get("detail") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(detail, dict):
            continue
        if (
            detail.get("migration_id") != migration_id
            or detail.get("manifest_version") != version
        ):
            continue
        row_id = int(str(row["target"]).split(":")[-1])
        result.setdefault(row_id, []).append({**dict(row), "detail_json": detail})
    return result


def _edd_links(db: Any, row_id: int) -> list[dict[str, Any]]:
    return db.execute(
        """
        SELECT id AS edd_case_id, application_id, linked_monitoring_alert_id,
               stage
          FROM edd_cases
         WHERE linked_monitoring_alert_id = ?
         ORDER BY id
        """,
        (row_id,),
    ).fetchall()


def _row_mismatches(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    expected_status: str,
) -> list[dict[str, Any]]:
    comparisons = {
        "application_id": expected.get("application_id"),
        "status": expected_status,
        "effective_updated_at": expected.get("effective_updated_at"),
    }
    for optional in (
        "application_ref",
        "alert_type",
        "severity",
        "provider",
        "case_identifier",
        "source_reference",
        "officer_action",
    ):
        if optional in expected:
            comparisons[optional] = expected.get(optional)

    actual = dict(observed)
    actual["effective_updated_at"] = _effective_updated_at(observed)
    mismatches: list[dict[str, Any]] = []
    for field, wanted in comparisons.items():
        got = actual.get(field)
        if got != wanted:
            mismatches.append(
                {
                    "field": field,
                    "expected": _redacted_value(wanted),
                    "observed": _redacted_value(got),
                }
            )
    return mismatches


def _safe_status_counts(
    raw_counts: Mapping[str, int], allowed_statuses: set[str]
) -> dict[str, int]:
    result: dict[str, int] = {}
    for status, count in sorted(raw_counts.items()):
        label = (
            status
            if status in allowed_statuses
            else f"unexpected:{stable_hash(status)[:16]}"
        )
        result[label] = result.get(label, 0) + int(count)
    return result


def _flags_off(
    manifest: Mapping[str, Any], feature_state: Mapping[str, Any]
) -> list[dict[str, Any]]:
    expected = manifest["baseline"]["expected_feature_flags"]
    mismatches = []
    for flag in GOVERNED_FLAGS:
        observed = feature_state.get(flag)
        if observed is not False or expected.get(flag) is not False:
            mismatches.append(
                {"flag": flag, "expected": False, "observed": observed}
            )
    return mismatches


def build_plan(
    db: Any,
    manifest: Mapping[str, Any],
    feature_state: Mapping[str, Any],
    *,
    lock_rows: bool = False,
) -> dict[str, Any]:
    """Build a deterministic plan without committing or mutating any row."""
    validate_manifest(manifest)
    expected_by_id = _rows_by_id(manifest)
    observed_rows = _fetch_alerts(db, for_update=lock_rows)
    observed_by_id = {int(row["id"]): row for row in observed_rows}
    automatic_ids = sorted(
        row_id
        for row_id, row in expected_by_id.items()
        if row["disposition"] == "automatic"
    )
    available_audit_columns = _audit_columns(db)
    missing_audit_columns = sorted(REQUIRED_AUDIT_COLUMNS - available_audit_columns)
    migration_audits = (
        _migration_audits(db, manifest, automatic_ids)
        if not missing_audit_columns
        else {row_id: [] for row_id in automatic_ids}
    )

    blockers: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    already_canonical: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    unchanged: list[int] = []

    expected_ids = set(expected_by_id)
    observed_ids = set(observed_by_id)
    for row_id in sorted(expected_ids - observed_ids):
        blockers.append({"id": row_id, "reason": "audited row is missing"})
    for row_id in sorted(observed_ids - expected_ids):
        blockers.append({"id": row_id, "reason": "unexpected row not present in PR #900"})

    for row_id in sorted(expected_ids & observed_ids):
        expected = expected_by_id[row_id]
        observed = observed_by_id[row_id]
        source = expected["source_status"]
        target = expected.get("target_status")
        disposition = expected["disposition"]
        status = observed.get("status")

        if disposition == "automatic":
            audits = migration_audits.get(row_id, [])
            apply_audits = [a for a in audits if a["action"] == MIGRATION_ACTION]
            rollback_audits = [a for a in audits if a["action"] == ROLLBACK_ACTION]
            if status == source:
                mismatch = _row_mismatches(observed, expected, expected_status=source)
                if mismatch:
                    blockers.append(
                        {"id": row_id, "reason": "row preconditions changed", "mismatches": mismatch}
                    )
                    continue
                if apply_audits or rollback_audits:
                    blockers.append(
                        {
                            "id": row_id,
                            "reason": "source state has prior migration audit; manifest version cannot be replayed",
                        }
                    )
                    continue
                required_link = expected.get("required_edd_link")
                if required_link:
                    observed_links = _edd_links(db, row_id)
                    link_mismatch = []
                    if len(observed_links) != 1:
                        link_mismatch.append(
                            {
                                "field": "edd_link_count",
                                "expected": 1,
                                "observed": len(observed_links),
                            }
                        )
                    else:
                        observed_link = observed_links[0]
                        for field, wanted in required_link.items():
                            if observed_link.get(field) != wanted:
                                link_mismatch.append(
                                    {
                                        "field": f"edd_link.{field}",
                                        "expected": _redacted_value(wanted),
                                        "observed": _redacted_value(
                                            observed_link.get(field)
                                        ),
                                    }
                                )
                    if link_mismatch:
                        blockers.append(
                            {"id": row_id, "reason": "required EDD linkage changed", "mismatches": link_mismatch}
                        )
                        continue
                eligible.append(
                    {
                        "id": row_id,
                        "application_id": expected["application_id"],
                        "current_status": source,
                        "proposed_status": target,
                        "reason": expected["mapping_reason"],
                    }
                )
            elif status == target:
                mismatch = _row_mismatches(observed, expected, expected_status=target)
                if mismatch:
                    blockers.append(
                        {"id": row_id, "reason": "post-migration row changed", "mismatches": mismatch}
                    )
                elif len(apply_audits) != 1 or rollback_audits:
                    blockers.append(
                        {
                            "id": row_id,
                            "reason": "canonical target has missing, duplicate, or rolled-back migration audit",
                            "apply_audit_count": len(apply_audits),
                            "rollback_audit_count": len(rollback_audits),
                        }
                    )
                else:
                    audit = apply_audits[0]
                    stored_fingerprint = audit["detail_json"].get(
                        "dry_run_plan_fingerprint"
                    )
                    invalid_fields = []
                    if _validated_sha256(stored_fingerprint) is None:
                        invalid_fields.append("dry_run_plan_fingerprint")
                    if _validated_sha256(audit.get("entry_hash")) is None:
                        invalid_fields.append("entry_hash")
                    if invalid_fields:
                        blockers.append(
                            {
                                "id": row_id,
                                "reason": "migration audit hash evidence is malformed",
                                "invalid_fields": invalid_fields,
                            }
                        )
                    already_canonical.append(
                        {
                            "id": row_id,
                            "status": target,
                            "source": "completed_migration",
                            "audit_id": audit["id"],
                            "dry_run_plan_fingerprint": _safe_hash_observation(
                                stored_fingerprint
                            ),
                            "entry_hash": _safe_hash_observation(
                                audit.get("entry_hash")
                            ),
                        }
                    )
            else:
                blockers.append(
                    {
                        "id": row_id,
                        "reason": "automatic row has unexpected status",
                        "expected_source": source,
                        "expected_target": target,
                        "observed": _redacted_value(status),
                    }
                )
        elif disposition == "manual_review":
            mismatch = _row_mismatches(observed, expected, expected_status=source)
            if mismatch:
                blockers.append(
                    {"id": row_id, "reason": "manual-review row drifted", "mismatches": mismatch}
                )
            manual_review.append(
                {
                    "id": row_id,
                    "current_status": (
                        source
                        if status == source
                        else f"unexpected:{stable_hash(status)[:16]}"
                    ),
                    "future_status": target,
                    "reason": expected["reason"],
                }
            )
            unchanged.append(row_id)
        else:
            mismatch = _row_mismatches(observed, expected, expected_status=source)
            if mismatch:
                blockers.append(
                    {"id": row_id, "reason": "canonical row drifted", "mismatches": mismatch}
                )
            already_canonical.append(
                {
                    "id": row_id,
                    "status": (
                        source
                        if status == source
                        else f"unexpected:{stable_hash(status)[:16]}"
                    ),
                    "source": "PR #900 already canonical",
                }
            )
            unchanged.append(row_id)

    raw_status_counts = dict(
        sorted(Counter(str(r.get("status")) for r in observed_rows).items())
    )
    baseline_counts = dict(sorted(manifest["baseline"]["expected_status_counts"].items()))
    expected_post_counts = dict(baseline_counts)
    for mapping in manifest["automatic_mappings"]:
        expected_post_counts[mapping["source_status"]] -= mapping["expected_count"]
        if expected_post_counts[mapping["source_status"]] == 0:
            expected_post_counts.pop(mapping["source_status"])
        expected_post_counts[mapping["target_status"]] = (
            expected_post_counts.get(mapping["target_status"], 0) + mapping["expected_count"]
        )
    expected_post_counts = dict(sorted(expected_post_counts.items()))

    phase = "drifted"
    if raw_status_counts == baseline_counts:
        phase = "pre_migration"
    elif raw_status_counts == expected_post_counts:
        phase = "post_migration"
    else:
        known_statuses = set(baseline_counts) | set(expected_post_counts)
        blockers.append(
            {
                "reason": "status counts differ from both approved states",
                "expected_pre": baseline_counts,
                "expected_post": expected_post_counts,
                "observed": _safe_status_counts(raw_status_counts, known_statuses),
            }
        )

    if len(observed_rows) != int(manifest["baseline"]["expected_alert_count"]):
        blockers.append(
            {
                "reason": "alert count mismatch",
                "expected": manifest["baseline"]["expected_alert_count"],
                "observed": len(observed_rows),
            }
        )

    flag_mismatches = _flags_off(manifest, feature_state)
    blockers.extend({"reason": "Monitoring feature flag is not OFF", **item} for item in flag_mismatches)

    if missing_audit_columns:
        blockers.append(
            {"reason": "canonical audit writer infrastructure unavailable", "missing_columns": missing_audit_columns}
        )

    expected_digest = manifest["baseline"].get("postgres_full_row_md5")
    observed_digest = _full_row_md5(
        db, manifest, normalize_applied=(phase == "post_migration")
    )
    if expected_digest and not observed_digest:
        blockers.append(
            {
                "reason": "required PostgreSQL full-row fingerprint is unavailable",
                "expected_normalized_md5": expected_digest,
            }
        )
    elif expected_digest and observed_digest != expected_digest:
        blockers.append(
            {
                "reason": "full monitoring_alerts fingerprint changed",
                "expected_normalized_md5": expected_digest,
                "observed_normalized_md5": observed_digest,
            }
        )

    expected_eligible = sum(
        int(mapping["expected_count"]) for mapping in manifest["automatic_mappings"]
    )
    if phase == "pre_migration" and len(eligible) != expected_eligible:
        blockers.append(
            {
                "reason": "eligible count mismatch",
                "expected": expected_eligible,
                "observed": len(eligible),
            }
        )
    if phase == "post_migration" and eligible:
        blockers.append(
            {"reason": "post-migration state still contains eligible source rows"}
        )

    fingerprint_payload = {
        "migration_id": manifest["migration_id"],
        "manifest_version": manifest["manifest_version"],
        "phase": phase,
        "total_rows_scanned": len(observed_rows),
        "eligible_rows": eligible,
        "already_canonical_rows": already_canonical,
        "manual_review_rows": manual_review,
        "duplicate_alert_ids": manifest.get("duplicate_alert_ids", []),
        "orphan_alert_ids": manifest.get("orphan_alert_ids", []),
        "unchanged_row_ids": sorted(unchanged),
        "expected_status_counts": (
            baseline_counts if phase == "pre_migration" else expected_post_counts
        ),
        "observed_status_counts": _safe_status_counts(
            raw_status_counts, set(baseline_counts) | set(expected_post_counts)
        ),
        "normalized_monitoring_alerts_md5": observed_digest,
        "feature_flags": {flag: feature_state.get(flag) for flag in GOVERNED_FLAGS},
        "blocked_by_changed_preconditions": blockers,
    }
    plan = dict(fingerprint_payload)
    plan["expected_eligible_count"] = expected_eligible
    plan["observed_eligible_count"] = len(eligible)
    plan["rows_that_would_be_unchanged"] = len(observed_rows) - len(eligible)
    plan["plan_fingerprint"] = stable_hash(fingerprint_payload)
    plan["read_only"] = True
    plan["safe_to_apply"] = phase == "pre_migration" and not blockers
    return plan


def _begin_write_transaction(db: Any) -> None:
    # get_db() validates pooled PostgreSQL connections with SELECT 1; psycopg2
    # leaves that harmless pre-ping transaction open. This operator-only tool
    # owns its dedicated connection, so clear the checkout transaction before
    # declaring the migration transaction's isolation and lock contract.
    db.rollback()
    if getattr(db, "is_postgres", False):
        db.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE")
        # Lock both regulated rows and every audit writer (including legacy raw
        # INSERT paths) before the serializable snapshot. This closes the
        # rollback later-audit TOCTOU window.
        db.execute(
            "LOCK TABLE monitoring_alerts, audit_log "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    else:
        db.execute("PRAGMA query_only = OFF")
        db.execute("BEGIN IMMEDIATE")


def begin_read_only_transaction(db: Any) -> None:
    """Ask the database engine to enforce the planner's no-write contract."""
    # See _begin_write_transaction: clear get_db()'s pre-ping transaction.
    db.rollback()
    if getattr(db, "is_postgres", False):
        db.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
    else:
        db.execute("PRAGMA query_only = ON")


def _rollback_quietly(db: Any) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _verified_test_sqlite(db: Any) -> bool:
    return (
        not getattr(db, "is_postgres", False)
        and getattr(db, "database_identity", None) == ":memory:"
        and bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def _require_mutation_engine(db: Any, manifest: Mapping[str, Any]) -> None:
    """Production mutation is PostgreSQL-only with a mandatory PR #900 digest."""
    if (
        _verified_test_sqlite(db)
        and not manifest.get("baseline", {}).get("postgres_full_row_md5")
    ):
        return
    if not getattr(db, "is_postgres", False):
        raise ReconciliationError(
            "controlled Monitoring status mutation requires PostgreSQL"
        )
    if not manifest.get("baseline", {}).get("postgres_full_row_md5"):
        raise ReconciliationError(
            "approved PostgreSQL full-row fingerprint is required"
        )


def apply_backfill(
    db: Any,
    manifest: Mapping[str, Any],
    feature_state: Mapping[str, Any],
    *,
    actor: str,
    expected_plan_fingerprint: str,
    approval_phrase: str,
    audit_writer: Optional[Callable[..., str]] = None,
) -> dict[str, Any]:
    """Apply the exact plan atomically or change nothing."""
    validate_approved_manifest(manifest)
    _require_mutation_engine(db, manifest)
    if approval_phrase != APPROVAL_PHRASE:
        raise ReconciliationError("exact founder approval phrase is required")
    if not str(actor or "").strip():
        raise ReconciliationError("operator identity is required")
    if not expected_plan_fingerprint:
        raise ReconciliationError("approved dry-run plan fingerprint is required")
    if audit_writer is None:
        try:
            from db import append_audit_log as audit_writer
        except Exception as exc:  # pragma: no cover - import failure is environment-specific
            raise ReconciliationError("canonical audit writer is unavailable") from exc

    committed = False
    changed_rows: list[dict[str, Any]] = []
    audit_entries: list[dict[str, Any]] = []
    post_plan: Optional[dict[str, Any]] = None
    try:
        _begin_write_transaction(db)
        plan = build_plan(db, manifest, feature_state, lock_rows=True)
        if plan["phase"] == "post_migration" and not plan["blocked_by_changed_preconditions"]:
            completed = [
                row
                for row in plan["already_canonical_rows"]
                if row.get("source") == "completed_migration"
            ]
            if (
                not completed
                or any(
                    row.get("dry_run_plan_fingerprint")
                    != expected_plan_fingerprint
                    for row in completed
                )
            ):
                raise ReconciliationError(
                    "approved fingerprint does not match the recorded completed migration",
                    plan=plan,
                )
            db.rollback()
            return {
                "migration_id": manifest["migration_id"],
                "manifest_version": manifest["manifest_version"],
                "changed_rows": [],
                "audit_entries": [],
                "idempotent": True,
                "post_migration_plan": plan,
            }
        if not plan["safe_to_apply"]:
            raise ReconciliationError("reconciliation preconditions failed", plan=plan)
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise ReconciliationError(
                "approved fingerprint differs from locked transaction plan", plan=plan
            )

        for eligible in plan["eligible_rows"]:
            row_id = int(eligible["id"])
            source = eligible["current_status"]
            target = eligible["proposed_status"]
            db.execute(
                """
                UPDATE monitoring_alerts
                   SET status = ?
                 WHERE id = ?
                   AND status = ?
                """,
                (target, row_id, source),
            )
            cursor = getattr(db, "_cursor", None)
            if cursor is None or cursor.rowcount != 1:
                raise ReconciliationError(
                    f"status update count mismatch for alert {row_id}", plan=plan
                )
            detail = {
                "migration_id": manifest["migration_id"],
                "manifest_version": manifest["manifest_version"],
                "pr_identifier": manifest["pr_identifier"],
                "reason": eligible["reason"],
                "actor": actor,
                "dry_run_plan_fingerprint": plan["plan_fingerprint"],
            }
            entry_hash = audit_writer(
                db,
                action=MIGRATION_ACTION,
                user_id=actor,
                user_name=actor,
                user_role="migration_operator",
                target=f"monitoring_alert:{row_id}",
                detail=canonical_json(detail),
                before_state={
                    "id": row_id,
                    "status": source,
                    "migration_id": manifest["migration_id"],
                },
                after_state={
                    "id": row_id,
                    "status": target,
                    "manifest_version": manifest["manifest_version"],
                    "dry_run_plan_fingerprint": plan["plan_fingerprint"],
                },
                application_id=eligible["application_id"],
                commit=False,
            )
            validated_entry_hash = _validated_sha256(entry_hash)
            if validated_entry_hash is None:
                raise ReconciliationError(
                    f"audit writer returned malformed entry hash for alert {row_id}",
                    plan=plan,
                )
            changed_rows.append(
                {"id": row_id, "previous_status": source, "new_status": target}
            )
            audit_entries.append(
                {
                    "id": row_id,
                    "action": MIGRATION_ACTION,
                    "entry_hash": validated_entry_hash,
                }
            )

        post_plan = build_plan(db, manifest, feature_state, lock_rows=True)
        if (
            post_plan["phase"] != "post_migration"
            or post_plan["eligible_rows"]
            or post_plan["blocked_by_changed_preconditions"]
        ):
            raise ReconciliationError(
                "post-migration reconciliation failed inside transaction",
                plan=post_plan,
            )
        if len(audit_entries) != len(changed_rows):
            raise ReconciliationError("audit count does not match mutation count")
        db.commit()
        committed = True

        # Separate, database-enforced post-commit evidence. It cannot make the
        # atomic commit safer (the in-transaction reconciliation above does
        # that); it proves the newly committed state is what a fresh snapshot
        # observes after locks are released.
        begin_read_only_transaction(db)
        post_commit_plan = build_plan(db, manifest, feature_state)
        db.rollback()
        if _verified_test_sqlite(db):
            db.execute("PRAGMA query_only = OFF")
        if (
            post_commit_plan["phase"] != "post_migration"
            or post_commit_plan["eligible_rows"]
            or post_commit_plan["blocked_by_changed_preconditions"]
        ):
            raise PostCommitReconciliationError(
                "migration committed but post-commit reconciliation failed",
                plan=post_commit_plan,
                changed_rows=changed_rows,
                audit_entries=audit_entries,
            )
        return {
            "migration_id": manifest["migration_id"],
            "manifest_version": manifest["manifest_version"],
            "dry_run_plan_fingerprint": plan["plan_fingerprint"],
            "changed_rows": changed_rows,
            "audit_entries": audit_entries,
            "idempotent": False,
            "post_migration_plan": post_plan,
            "post_commit_plan": post_commit_plan,
        }
    except Exception as exc:
        _rollback_quietly(db)
        if committed and not isinstance(exc, PostCommitReconciliationError):
            raise PostCommitReconciliationError(
                "migration committed but post-commit reconciliation could not complete",
                plan=getattr(exc, "plan", post_plan),
                changed_rows=changed_rows,
                audit_entries=audit_entries,
            ) from exc
        raise


def build_rollback_plan(
    db: Any,
    manifest: Mapping[str, Any],
    feature_state: Mapping[str, Any],
    *,
    lock_rows: bool = False,
) -> dict[str, Any]:
    """Plan a narrow rollback and refuse after any later alert mutation."""
    current = build_plan(db, manifest, feature_state, lock_rows=lock_rows)
    expected_by_id = _rows_by_id(manifest)
    automatic_ids = sorted(
        row_id
        for row_id, row in expected_by_id.items()
        if row["disposition"] == "automatic"
    )
    audits = _migration_audits(db, manifest, automatic_ids)
    blockers = list(current["blocked_by_changed_preconditions"])
    eligible = []
    already_restored: list[dict[str, Any]] = []

    for row_id in automatic_ids:
        expected = expected_by_id[row_id]
        entries = audits.get(row_id, [])
        applied = [row for row in entries if row["action"] == MIGRATION_ACTION]
        rolled_back = [row for row in entries if row["action"] == ROLLBACK_ACTION]
        observed = db.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?", (row_id,)
        ).fetchone()
        status = observed.get("status") if observed else None
        if status == expected["source_status"] and len(applied) == 1 and len(rolled_back) == 1:
            blockers = [
                item
                for item in blockers
                if not (
                    item.get("id") == row_id
                    and item.get("reason")
                    == "source state has prior migration audit; manifest version cannot be replayed"
                )
            ]
            rollback_fingerprint = rolled_back[0]["detail_json"].get(
                "rollback_plan_fingerprint"
            )
            invalid_fields = []
            if _validated_sha256(rollback_fingerprint) is None:
                invalid_fields.append("rollback_plan_fingerprint")
            if _validated_sha256(rolled_back[0].get("entry_hash")) is None:
                invalid_fields.append("entry_hash")
            if invalid_fields:
                blockers.append(
                    {
                        "id": row_id,
                        "reason": "rollback audit hash evidence is malformed",
                        "invalid_fields": invalid_fields,
                    }
                )
            already_restored.append(
                {
                    "id": row_id,
                    "rollback_plan_fingerprint": _safe_hash_observation(
                        rollback_fingerprint
                    ),
                    "entry_hash": _safe_hash_observation(
                        rolled_back[0].get("entry_hash")
                    ),
                }
            )
            continue
        if status != expected["target_status"] or len(applied) != 1 or rolled_back:
            blockers.append(
                {
                    "id": row_id,
                    "reason": "row is not in the exact once-applied rollback state",
                    "status": _redacted_value(status),
                    "apply_audit_count": len(applied),
                    "rollback_audit_count": len(rolled_back),
                }
            )
            continue
        later = db.execute(
            """
            SELECT id, action, timestamp
              FROM audit_log
             WHERE target = ?
               AND id > ?
             ORDER BY id
            """,
            (f"monitoring_alert:{row_id}", applied[0]["id"]),
        ).fetchall()
        if later:
            blockers.append(
                {
                    "id": row_id,
                    "reason": "later alert audit evidence exists; automated rollback refused",
                    "later_audit_ids": [row["id"] for row in later],
                }
            )
            continue
        migration_entry_hash = _validated_sha256(applied[0].get("entry_hash"))
        original_plan_fingerprint = _validated_sha256(
            applied[0]["detail_json"].get("dry_run_plan_fingerprint")
        )
        invalid_fields = []
        if migration_entry_hash is None:
            invalid_fields.append("entry_hash")
        if original_plan_fingerprint is None:
            invalid_fields.append("dry_run_plan_fingerprint")
        if invalid_fields:
            blockers.append(
                {
                    "id": row_id,
                    "reason": "migration audit hash evidence is malformed",
                    "invalid_fields": invalid_fields,
                }
            )
            continue
        eligible.append(
            {
                "id": row_id,
                "current_status": expected["target_status"],
                "rollback_status": expected["rollback_target"],
                "application_id": expected["application_id"],
                "migration_audit_id": applied[0]["id"],
                "migration_entry_hash": migration_entry_hash,
                "original_plan_fingerprint": original_plan_fingerprint,
            }
        )

    if len(already_restored) == len(automatic_ids):
        blockers = [
            item
            for item in blockers
            if item.get("reason") != "eligible count mismatch"
        ]

    payload = {
        "migration_id": manifest["migration_id"],
        "manifest_version": manifest["manifest_version"],
        "eligible_rows": eligible,
        "already_restored_rows": already_restored,
        "blocked_by_changed_preconditions": blockers,
        "feature_flags": {flag: feature_state.get(flag) for flag in GOVERNED_FLAGS},
    }
    result = dict(payload)
    result["rollback_plan_fingerprint"] = stable_hash(payload)
    result["read_only"] = True
    result["safe_to_rollback"] = bool(eligible) and not blockers
    return result


def apply_rollback(
    db: Any,
    manifest: Mapping[str, Any],
    feature_state: Mapping[str, Any],
    *,
    actor: str,
    expected_rollback_fingerprint: str,
    audit_writer: Optional[Callable[..., str]] = None,
) -> dict[str, Any]:
    validate_approved_manifest(manifest)
    _require_mutation_engine(db, manifest)
    if not str(actor or "").strip():
        raise ReconciliationError("rollback operator identity is required")
    if not expected_rollback_fingerprint:
        raise ReconciliationError("approved rollback fingerprint is required")
    if audit_writer is None:
        from db import append_audit_log as audit_writer

    try:
        _begin_write_transaction(db)
        plan = build_rollback_plan(
            db, manifest, feature_state, lock_rows=True
        )
        if plan["already_restored_rows"] and not plan["eligible_rows"] and not plan[
            "blocked_by_changed_preconditions"
        ]:
            if any(
                row.get("rollback_plan_fingerprint")
                != expected_rollback_fingerprint
                for row in plan["already_restored_rows"]
            ):
                raise ReconciliationError(
                    "rollback fingerprint does not match the recorded rollback",
                    plan=plan,
                )
            db.rollback()
            return {"changed_rows": [], "idempotent": True, "rollback_plan": plan}
        if (
            not plan["safe_to_rollback"]
            or plan["rollback_plan_fingerprint"] != expected_rollback_fingerprint
        ):
            raise ReconciliationError("rollback preconditions failed", plan=plan)

        changed_rows = []
        audit_entries = []
        for row in plan["eligible_rows"]:
            db.execute(
                "UPDATE monitoring_alerts SET status = ? WHERE id = ? AND status = ?",
                (row["rollback_status"], row["id"], row["current_status"]),
            )
            cursor = getattr(db, "_cursor", None)
            if cursor is None or cursor.rowcount != 1:
                raise ReconciliationError(
                    f"rollback update count mismatch for alert {row['id']}", plan=plan
                )
            detail = {
                "migration_id": manifest["migration_id"],
                "manifest_version": manifest["manifest_version"],
                "pr_identifier": manifest["pr_identifier"],
                "actor": actor,
                "migration_audit_id": row["migration_audit_id"],
                "migration_entry_hash": row["migration_entry_hash"],
                "original_plan_fingerprint": row["original_plan_fingerprint"],
                "rollback_plan_fingerprint": plan["rollback_plan_fingerprint"],
            }
            entry_hash = audit_writer(
                db,
                action=ROLLBACK_ACTION,
                user_id=actor,
                user_name=actor,
                user_role="migration_operator",
                target=f"monitoring_alert:{row['id']}",
                detail=canonical_json(detail),
                before_state={"id": row["id"], "status": row["current_status"]},
                after_state={
                    "id": row["id"],
                    "status": row["rollback_status"],
                    "rollback_plan_fingerprint": plan["rollback_plan_fingerprint"],
                },
                application_id=row["application_id"],
                commit=False,
            )
            validated_entry_hash = _validated_sha256(entry_hash)
            if validated_entry_hash is None:
                raise ReconciliationError(
                    f"audit writer returned malformed rollback entry hash for alert {row['id']}",
                    plan=plan,
                )
            changed_rows.append(
                {
                    "id": row["id"],
                    "previous_status": row["current_status"],
                    "new_status": row["rollback_status"],
                }
            )
            audit_entries.append(
                {
                    "id": row["id"],
                    "action": ROLLBACK_ACTION,
                    "entry_hash": validated_entry_hash,
                }
            )
        post_plan = build_rollback_plan(
            db, manifest, feature_state, lock_rows=True
        )
        if (
            post_plan["eligible_rows"]
            or post_plan["blocked_by_changed_preconditions"]
            or len(post_plan["already_restored_rows"]) != len(changed_rows)
        ):
            raise ReconciliationError(
                "post-rollback reconciliation failed inside transaction",
                plan=post_plan,
            )
        db.commit()
        return {
            "changed_rows": changed_rows,
            "audit_entries": audit_entries,
            "idempotent": False,
            "rollback_plan": plan,
            "post_rollback_plan": post_plan,
        }
    except Exception:
        _rollback_quietly(db)
        raise
