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
from urllib.parse import parse_qsl, unquote, urlsplit


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
    "test_database_teardown",
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
    database_identity: Optional[str] = None
    is_postgres: bool = False


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


def _truthy_environment_marker(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _normalise_postgres_test_dsn(value: Optional[str]):
    """Return a comparison tuple and parsed locality fields, or ``None``.

    Only URI-form PostgreSQL DSNs are accepted.  Keyword DSNs and ambiguous
    values fail closed rather than relying on driver-specific interpretation.
    """
    if not value:
        return None
    try:
        parts = urlsplit(str(value).strip())
        if parts.scheme.lower() not in {"postgres", "postgresql"} or parts.fragment:
            return None
        path = unquote(parts.path or "")
        if not path.startswith("/") or not path[1:] or "/" in path[1:]:
            return None
        database = path[1:]
        host = (parts.hostname or "").lower()
        port = parts.port or 5432
        query = tuple(sorted(parse_qsl(parts.query, keep_blank_values=True)))
        query_map = {}
        for key, item in query:
            query_map.setdefault(key.lower(), []).append(item)
        normalised = (
            "postgresql",
            parts.username or "",
            parts.password or "",
            host,
            port,
            database,
            query,
        )
        return normalised, host, database, query_map
    except (TypeError, ValueError):
        return None


def _local_postgres_host(host: str, query_map) -> bool:
    local_names = {"", "localhost", "127.0.0.1", "::1"}
    if host not in local_names:
        return False
    for key in ("host", "hostaddr"):
        for override in query_map.get(key, []):
            candidate = str(override or "").strip().lower()
            if candidate in local_names:
                continue
            # libpq local-socket directories are absolute filesystem paths.
            if key == "host" and candidate.startswith("/") and ".." not in Path(candidate).parts:
                continue
            return False
    return True


def is_verified_disposable_postgres_test_db(
    database_identity: Optional[str],
    is_postgres: bool,
    *,
    environment: Optional[str] = None,
    test_postgres_dsn: Optional[str] = None,
) -> bool:
    """Prove a PostgreSQL database is the explicit local disposable test DB."""
    if not is_postgres or _environment(environment) != "testing":
        return False
    expected_dsn = test_postgres_dsn or os.environ.get("TEST_POSTGRES_DSN")
    if not expected_dsn or not database_identity:
        return False

    # Independent deployment markers override a misleading ENVIRONMENT value.
    if any(
        _truthy_environment_marker(os.environ.get(key))
        for key in ("PRODUCTION", "IS_PRODUCTION", "STAGING", "IS_STAGING")
    ):
        return False
    for key in ("APP_ENV", "DEPLOYMENT_ENVIRONMENT"):
        if str(os.environ.get(key) or "").strip().lower() in {
            "prod", "production", "stage", "staging"
        }:
            return False

    active = _normalise_postgres_test_dsn(database_identity)
    expected = _normalise_postgres_test_dsn(expected_dsn)
    if active is None or expected is None or active[0] != expected[0]:
        return False
    _, host, database, query_map = active
    if not _local_postgres_host(host, query_map):
        return False
    database_lower = database.lower()
    if not database_lower.startswith("onboarda_test_"):
        return False
    if any(marker in database_lower for marker in ("production", "staging", "_prod", "_stage")):
        return False
    return True


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
    elif context.name == "test_database_teardown":
        if context.role != "system" or not context.confirmed:
            raise ValueError("test database teardown requires explicit system test authority")
        if not is_verified_disposable_postgres_test_db(
            context.database_identity,
            context.is_postgres,
            environment=context.environment,
        ):
            raise ValueError("test database teardown requires a verified disposable local PostgreSQL test database")


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
    database_identity: Optional[str] = None,
    is_postgres: bool = False,
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
        database_identity=database_identity,
        is_postgres=bool(is_postgres),
    )
    _validate_context(context)
    token = _ACTIVE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_CONTEXT.reset(token)


@contextmanager
def test_database_teardown_context(db, *, reason: str):
    """Authorize cleanup only on the explicitly configured disposable PG DB.

    No runtime handler imports this helper.  It exists for pytest reset and
    teardown code whose direct regulated DELETE statements must clean a fresh,
    local, run-scoped PostgreSQL database.
    """
    with sanctioned_delete_context(
        "test_database_teardown",
        actor_id="pytest:test-database-teardown",
        role="system",
        reason=reason,
        allowed_tables=REGULATED_TABLES,
        environment=os.environ.get("ENVIRONMENT"),
        confirmed=True,
        database_identity=getattr(db, "database_identity", None),
        is_postgres=bool(getattr(db, "is_postgres", False)),
    ) as context:
        yield context


# Prevent pytest from collecting this imported helper as a test function.
test_database_teardown_context.__test__ = False


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
