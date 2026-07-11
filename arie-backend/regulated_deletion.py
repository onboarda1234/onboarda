"""Runtime protection for regulated-record hard deletion.

P12-1 Phase B deliberately protects concrete regulated evidence tables at the
shared database wrapper.  Business/root tables (for example ``applications``
and ``documents``) require record-aware checks at their runtime choke points;
they are not treated as globally regulated tables here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple


logger = logging.getLogger("arie.regulated_deletion")

FIXTURE_CLEANUP_CONFIRMATION = "DELETE-SYNTHETIC-FIXTURE"

# Exact table names only.  Root/business tables use record-aware preflight
# guards because a blanket denial would also prevent safe deletion of a new,
# evidence-free draft.
REGULATED_TABLES = frozenset({
    "agent_executions",
    "ai_agents",
    "ai_checks",
    "application_corrections",
    "application_enhanced_requirements",
    "audit_log",
    "change_alerts",
    "change_request_documents",
    "change_request_items",
    "change_request_reviews",
    "change_requests",
    "compliance_memos",
    "compliance_resources",
    "company_registry_lookups",
    "complyadvantage_webhook_deliveries",
    "country_risk_entries",
    "country_risk_snapshots",
    "data_purge_log",
    "data_retention_policies",
    "data_subject_requests",
    "decision_records",
    "edd_cases",
    "edd_findings",
    "edd_memo_attachments",
    "enhanced_requirement_rules",
    "entity_profile_versions",
    "gdpr_erasure_log",
    "idv_resolutions",
    "monitoring_alert_escalations",
    "monitoring_alert_evidence",
    "monitoring_alert_followups",
    "monitoring_alert_review_requests",
    "monitoring_alerts",
    "periodic_review_evidence_links",
    "periodic_review_memos",
    "periodic_reviews",
    "regulatory_documents",
    "risk_config",
    "rmi_request_items",
    "rmi_requests",
    "sar_reports",
    "screening_provider_comparisons",
    "screening_monitoring_subscriptions",
    "screening_reports_normalized",
    "screening_reviews",
    "screening_state_integrity_backfill_log",
    "sumsub_applicant_mappings",
    "sumsub_unmatched_webhooks",
    "supervisor_audit_log",
    "supervisor_audit_migrations",
    "supervisor_contradictions",
    "supervisor_escalations",
    "supervisor_human_reviews",
    "supervisor_overrides",
    "supervisor_pipeline_results",
    "supervisor_rule_evaluations",
    "supervisor_rules_config",
    "supervisor_run_outputs",
    "supervisor_runs",
    "supervisor_validation_results",
    "system_settings",
    "transactions",
})

# These operational records do not contain compliance decisions/evidence.
# The allowlist is documentary: the interceptor only denies REGULATED_TABLES.
EPHEMERAL_TABLES = frozenset({
    "account_lockouts",
    "client_sessions",
    "data_migration_markers",
    "monitoring_agent_status",
    "notifications",
    "rate_limits",
    "revoked_tokens",
    "schema_migrations",
    "schema_version",
    "sessions",
    "shared_rate_limits",
    "verification_jobs",
})

APPROVED_CONTEXTS = frozenset({
    "retention_purge",
    "future_gdpr_erasure_dual_control",
    "fixture_cleanup_nonprod",
    "migration_admin_context",
})


class RegulatedDeleteDenied(RuntimeError):
    """Raised before a regulated hard-delete can mutate the database."""

    def __init__(self, table: str, reason: str, context_name: Optional[str] = None):
        self.table = table
        self.reason = reason
        self.context_name = context_name
        super().__init__(f"regulated delete denied for {table}: {reason}")


@dataclass(frozen=True)
class DeletionContext:
    name: str
    actor_id: str
    role: str
    reason: str
    request_id: Optional[str] = None
    application_id: Optional[str] = None
    allowed_tables: Tuple[str, ...] = ()
    environment: Optional[str] = None
    is_fixture: bool = False
    confirmed: bool = False
    second_approver_id: Optional[str] = None
    feature_enabled: bool = False


_ACTIVE_CONTEXT: ContextVar[Optional[DeletionContext]] = ContextVar(
    "regulated_deletion_context", default=None
)


def _normalise_table(table: str) -> str:
    return str(table or "").strip().strip('`"[]').split(".")[-1].strip('`"[]').lower()


def is_regulated_table(table: str) -> bool:
    return _normalise_table(table) in REGULATED_TABLES


def is_ephemeral_table(table: str) -> bool:
    return _normalise_table(table) in EPHEMERAL_TABLES


def _environment(value: Optional[str] = None) -> str:
    return (value or os.environ.get("ENVIRONMENT") or "development").strip().lower()


def _validate_context(context: DeletionContext) -> None:
    if context.name not in APPROVED_CONTEXTS:
        raise ValueError(f"unknown sanctioned deletion context: {context.name}")
    if not context.actor_id or not context.reason:
        raise ValueError("sanctioned deletion requires actor_id and reason")
    if not context.allowed_tables:
        raise ValueError("sanctioned deletion requires an explicit allowed_tables scope")
    if any(not is_regulated_table(table) for table in context.allowed_tables):
        raise ValueError("sanctioned deletion scope contains an unclassified table")

    env = _environment(context.environment)
    if context.name == "retention_purge":
        if context.role not in {"system", "admin", "sco"}:
            raise ValueError("retention purge requires system/admin/SCO authority")
        if not context.confirmed:
            raise ValueError("retention purge must be explicitly confirmed by the engine")
    elif context.name == "fixture_cleanup_nonprod":
        if env not in {"testing", "test", "staging"}:
            raise ValueError("fixture cleanup is permitted only in testing or staging")
        if not context.is_fixture or not context.confirmed:
            raise ValueError("fixture cleanup requires a fixture marker and explicit confirmation")
    elif context.name == "future_gdpr_erasure_dual_control":
        if not context.feature_enabled:
            raise ValueError("GDPR erasure remains disabled until its feature is approved")
        if context.role not in {"admin", "sco", "legal"}:
            raise ValueError("GDPR erasure requires legal/SCO/admin authority")
        if not context.confirmed or not context.second_approver_id:
            raise ValueError("GDPR erasure requires explicit dual control")
        if context.second_approver_id == context.actor_id:
            raise ValueError("GDPR erasure approvers must be distinct")
    elif context.name == "migration_admin_context":
        if context.role not in {"system", "admin"} or not context.confirmed:
            raise ValueError("migration/admin deletion requires explicit system/admin approval")


@contextmanager
def sanctioned_delete_context(
    name: str,
    *,
    actor_id: str,
    role: str,
    reason: str,
    allowed_tables: Iterable[str],
    request_id: Optional[str] = None,
    application_id: Optional[str] = None,
    environment: Optional[str] = None,
    is_fixture: bool = False,
    confirmed: bool = False,
    second_approver_id: Optional[str] = None,
    feature_enabled: bool = False,
):
    """Activate one validated, explicitly scoped sanctioned deletion context."""
    context = DeletionContext(
        name=name,
        actor_id=str(actor_id or ""),
        role=str(role or "").lower(),
        reason=str(reason or ""),
        request_id=request_id,
        application_id=application_id,
        allowed_tables=tuple(_normalise_table(t) for t in allowed_tables),
        environment=environment,
        is_fixture=bool(is_fixture),
        confirmed=bool(confirmed),
        second_approver_id=second_approver_id,
        feature_enabled=bool(feature_enabled),
    )
    _validate_context(context)
    token = _ACTIVE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_CONTEXT.reset(token)


def _safe_log_value(value: Optional[str], limit: int = 160) -> Optional[str]:
    if value is None:
        return None
    return " ".join(str(value).split())[:limit]


def log_regulated_delete_denied(
    table: str,
    reason: str,
    *,
    context: Optional[DeletionContext] = None,
    application_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """Write a structured operational event without logging SQL parameters/PII."""
    payload = {
        "event": "regulated_delete_denied",
        "table": _normalise_table(table),
        "reason": _safe_log_value(reason),
        "context": context.name if context else None,
        "actor_id": _safe_log_value(context.actor_id) if context else None,
        "role": _safe_log_value(context.role) if context else None,
        "application_id": _safe_log_value(application_id or (context.application_id if context else None)),
        "request_id": _safe_log_value(request_id or (context.request_id if context else None)),
    }
    logger.warning(json.dumps(payload, sort_keys=True))


def assert_regulated_delete_allowed(table: str) -> None:
    table_name = _normalise_table(table)
    if table_name not in REGULATED_TABLES:
        return
    context = _ACTIVE_CONTEXT.get()
    if context is not None and table_name in context.allowed_tables:
        return
    reason = "no sanctioned deletion context" if context is None else "table is outside sanctioned context scope"
    log_regulated_delete_denied(table_name, reason, context=context)
    raise RegulatedDeleteDenied(table_name, reason, context.name if context else None)


_DELETE_TABLE_RE = re.compile(
    r"\bDELETE\s+FROM\s+(?:(?:[\"`\[]?[A-Za-z_][A-Za-z0-9_$]*[\"`\]]?)\s*\.\s*)?"
    r"([\"`\[]?[A-Za-z_][A-Za-z0-9_$]*[\"`\]]?)",
    re.IGNORECASE,
)
_TRUNCATE_CLAUSE_RE = re.compile(
    r"\bTRUNCATE(?:\s+TABLE)?\s+(.+?)(?=\s+(?:RESTART|CONTINUE|CASCADE|RESTRICT)\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)


def _mask_sql_literals_and_comments(sql: str) -> str:
    """Mask single-quoted data and SQL comments before destructive parsing."""
    chars = list(sql)
    i = 0
    state = "sql"
    while i < len(chars):
        char = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if state == "sql":
            if char == "'":
                chars[i] = " "
                state = "string"
            elif char == "-" and nxt == "-":
                chars[i] = chars[i + 1] = " "
                i += 1
                state = "line_comment"
            elif char == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 1
                state = "block_comment"
        elif state == "string":
            chars[i] = " "
            if char == "'" and nxt == "'":
                chars[i + 1] = " "
                i += 1
            elif char == "'":
                state = "sql"
        elif state == "line_comment":
            if char in "\r\n":
                state = "sql"
            else:
                chars[i] = " "
        elif state == "block_comment":
            chars[i] = " "
            if char == "*" and nxt == "/":
                chars[i + 1] = " "
                i += 1
                state = "sql"
        i += 1
    return "".join(chars)


def is_verified_isolated_test_database(database_identity: Optional[str], is_postgres: bool) -> bool:
    """Allow legacy test teardown only for a provably isolated SQLite DB.

    A testing environment flag alone is intentionally insufficient.  PostgreSQL
    never receives this bypass because the connection identity is not enough to
    prove that a shared server/database is disposable.
    """
    if is_postgres or _environment() not in {"test", "testing"} or not database_identity:
        return False
    if database_identity == ":memory:":
        return True
    try:
        path = Path(database_identity).expanduser().resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        return path != temp_root and temp_root in path.parents
    except (OSError, RuntimeError, ValueError):
        return False


def assert_sql_delete_allowed(
    sql: str,
    *,
    database_identity: Optional[str] = None,
    is_postgres: bool = False,
) -> None:
    """Fail before execution when SQL targets a regulated table."""
    if is_verified_isolated_test_database(database_identity, is_postgres):
        return
    text = _mask_sql_literals_and_comments(str(sql or ""))
    tables = [match.group(1) for match in _DELETE_TABLE_RE.finditer(text)]
    for match in _TRUNCATE_CLAUSE_RE.finditer(text):
        for raw_table in match.group(1).split(","):
            tables.append(raw_table.strip().split(".")[-1])
    for table in tables:
        assert_regulated_delete_allowed(table)
