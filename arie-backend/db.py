"""
Database abstraction layer for Onboarda platform.
Supports both SQLite (development) and PostgreSQL (production).
"""

import os
import json
import sqlite3
import logging
import hashlib
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict, List, Tuple
import secrets
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MONITORING_ALERT_DISCOVERED_VIA_VALUES = (
    "webhook_live",
    "webhook_backfill",
    "manual_backfill",
    "manual",
    "officer_created",
    "document_health",
)

RMI_REQUEST_STATUS_VALUES = (
    "open",
    "pending_review",
    "partially_fulfilled",
    "fulfilled",
    "cancelled",
)

# ── P12-5 / DCI-006: closed value canons for workflow status/source columns ──
# Each tuple is the COMPLETE set of values any production code path writes
# (traced writer-by-writer; see migration_042 marker for the mapping).  These
# feed the CHECK constraints in both CREATE TABLE schemas and the v2.47
# constraint repair for existing PostgreSQL databases.  Adding a new status
# value REQUIRES updating the matching tuple here — the fresh-install CHECK
# will reject it in CI otherwise.

# clients.status: signup omits it (default), demo seed + smoke script write
# 'active'; 'inactive' is read-path-supported (login/reset filter on
# status='active') and is the operator deactivation state.  Mirrors the
# existing users.status CHECK.
CLIENT_STATUS_VALUES = ("active", "inactive")

# agent_executions.status: 'verified'/'flagged' (document verification),
# 'skipped' (agent disabled / no stored data), 'completed' (agent-3).  The
# remaining members are the canonical verification-state family
# (verification_state.py: 'pending','in_progress','failed') plus legacy
# 'error' — log_agent_execution is a public helper and demo/staging rows from
# older revisions may carry them.
AGENT_EXECUTION_STATUS_VALUES = (
    "verified",
    "flagged",
    "skipped",
    "completed",
    "pending",
    "in_progress",
    "failed",
    "error",
)

# agent_executions.source: 'ai' (DDL default, document verification) and
# agent-3's stored-screening interpretation source.
AGENT_EXECUTION_SOURCE_VALUES = ("ai", "stored_screening_results")

# supervisor_pipeline_results.status: single writer funnel
# (supervisor/supervisor.py run_pipeline → persist_pipeline_result); 'running'
# is the DDL default and must stay legal for any INSERT omitting the column.
SUPERVISOR_PIPELINE_STATUS_VALUES = (
    "running",
    "completed",
    "completed_with_errors",
    "awaiting_review",
    "failed",
)

# supervisor_audit_log.event_type: the full AuditEventType enum
# (supervisor/schemas.py) — every writer funnels through AuditLogger/pydantic
# or the verdict-chain helper, both enum-bound; the startup repair fallback
# writes 'system_error' (in the enum).  Kept in lockstep by a static test.
SUPERVISOR_AUDIT_EVENT_TYPE_VALUES = (
    "agent_run_started",
    "agent_run_completed",
    "agent_run_failed",
    "schema_validation_passed",
    "schema_validation_failed",
    "confidence_calculated",
    "confidence_routing",
    "contradiction_detected",
    "contradiction_resolved",
    "rule_triggered",
    "rule_overridden",
    "escalation_created",
    "escalation_assigned",
    "escalation_resolved",
    "human_review_started",
    "human_review_completed",
    "ai_override",
    "pipeline_started",
    "pipeline_completed",
    "pipeline_failed",
    "supervisor_verdict",
    "config_changed",
    "agent_version_changed",
    "prompt_version_changed",
    "system_error",
)

# supervisor_audit_log.severity: the Severity enum (supervisor/schemas.py)
# including 'warning' — six audit paths (ai_override, escalation_created,
# override human reviews, schema_validation_failed, contradiction/rule
# fallbacks) always intended to write it; the missing enum member is fixed in
# the same change (P12-5).
SUPERVISOR_AUDIT_SEVERITY_VALUES = (
    "critical",
    "high",
    "medium",
    "low",
    "info",
    "warning",
)

# compliance_memos.supervisor_status: 'pending' default + the closed
# run_memo_supervisor verdict enum; 'approved' is written by the gated staging
# fixture seeder (fixtures/seeder.py) against the real staging database.
COMPLIANCE_MEMO_SUPERVISOR_STATUS_VALUES = (
    "pending",
    "CONSISTENT",
    "CONSISTENT_WITH_WARNINGS",
    "INCONSISTENT",
    "approved",
)

# compliance_memos.rule_engine_status: only the DDL default and the staging
# fixture seeder's 'pass' ever reach this column today.  NOTE: the memo JSON
# carries a SAME-NAMED key with a different vocabulary
# ('CLEAN'/'ENFORCED'/'VIOLATIONS_DETECTED', memo_handler.py) — if that value
# is ever wired into this COLUMN, this canon (and the CHECKs) must be updated
# first; the fresh-install CHECK will fail CI on such a change by design.
COMPLIANCE_MEMO_RULE_ENGINE_STATUS_VALUES = ("pending", "pass")

FILE_MIGRATIONS_REQUIRING_RUNNER = frozenset({
    # Migration 020 is a data backfill. It is not represented by init_db's
    # schema DDL, so long-lived databases must let the file runner execute it.
    "020",
    # Migration 039 is a data fix (audit finding B1): it flips the seeded
    # session_tokens retention policy to auto_purge=FALSE on already-deployed
    # databases. Like 020 it is not represented by init_db DDL, so it must run
    # through the file runner rather than being pre-marked "covered by init_db".
    "039",
    # Migration 040 adds DSAR erasure-truth columns to existing databases. Fresh
    # installs already get the columns from init_db DDL; long-lived databases
    # must still run the file migration instead of treating it as covered.
    "040",
})

DSAR_ERASURE_TRUTH_COLUMNS = (
    "erasure_executed",
    "retention_outcome",
    "retained_until",
    "retained_categories",
    "erasure_notes",
)

_PR_CR1R_MANUAL_DEFAULTS_MARKER_KEY = "pr_cr1r_manual_defaults"
_PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY = "pr_pep_provider_detection_separation"

# Try to import psycopg2 for PostgreSQL support
try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


def _is_postgres_lock_timeout(exc: Exception) -> bool:
    if not PSYCOPG2_AVAILABLE:
        return False
    lock_error = getattr(getattr(psycopg2, "errors", None), "LockNotAvailable", None)
    return (
        getattr(exc, "pgcode", None) == "55P03"
        or (lock_error is not None and isinstance(exc, lock_error))
        or "lock timeout" in str(exc).lower()
    )


# ============================================================================
# Configuration (from unified config module)
# ============================================================================

from config import (
    DATABASE_URL,
    DB_PATH,
    ENVIRONMENT as _CFG_ENVIRONMENT,
    IS_DEMO as _CFG_IS_DEMO,
    ADMIN_INITIAL_PASSWORD as _CFG_ADMIN_INITIAL_PASSWORD,
)
USE_POSTGRESQL = bool(DATABASE_URL)

# PostgreSQL connection pool (initialized on first use)
_pg_pool = None  # Optional[pool.ThreadedConnectionPool]


# ============================================================================
# PostgreSQL Pool Management
# ============================================================================

def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def init_pg_pool():
    """Initialize PostgreSQL connection pool with production-safe timeouts.

    Timeouts prevent indefinite hangs during blue-green deploys when the old
    revision still holds database locks:
      connect_timeout  — TCP + auth handshake (seconds)
      statement_timeout — per-statement wall-clock limit (milliseconds)
      lock_timeout      — max wait for row/table locks (milliseconds)
    """
    global _pg_pool
    if _pg_pool is None and USE_POSTGRESQL:
        if not PSYCOPG2_AVAILABLE:
            raise ImportError(
                "psycopg2-binary is required for PostgreSQL support. "
                "Install it with: pip install psycopg2-binary --break-system-packages"
            )
        minconn = _env_int("PG_POOL_MINCONN", 1)
        maxconn = max(_env_int("PG_POOL_MAXCONN", 10), minconn)
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn, maxconn,
            DATABASE_URL,
            sslmode='require',
            connect_timeout=10,
            options='-c statement_timeout=30000 -c lock_timeout=10000',
        )
        logger.info(
            "PostgreSQL connection pool initialized (minconn=%s, maxconn=%s, "
            "connect_timeout=10s, statement_timeout=30s, lock_timeout=10s)",
            minconn,
            maxconn,
        )


def close_pg_pool():
    """Close PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.closeall()
        _pg_pool = None
        logger.info("PostgreSQL connection pool closed")


# ============================================================================
# Cross-task singleton scheduler locks (audit H9 / PR-14)
# ============================================================================
# Every ECS task runs the same Tornado PeriodicCallbacks, so before this fix
# every scheduled job (GDPR purge, monitoring automation, document health,
# memo recovery, PRS-6 notifications) executed once PER TASK per interval —
# duplicate purges and duplicate client notifications with 2+ tasks. Each
# tick now first takes a PostgreSQL session advisory lock on a DEDICATED
# (non-pooled) connection; whoever gets it runs, everyone else skips that
# tick. A dedicated connection makes release unconditional: closing it —
# including via process crash — releases the lock. Pooled connections are
# deliberately NOT used: session advisory locks survive putconn and would
# leak to the next borrower.

SCHEDULER_LOCK_KEYS = {
    "gdpr_purge": 8674309931,
    "monitoring_automation": 8674309932,
    "document_health": 8674309933,
    "memo_recovery": 8674309934,
    "prs6_notifications": 8674309935,
}


class SchedulerLockLease:
    """Holds (or reports failure to hold) one scheduler advisory lock."""

    def __init__(self, conn, acquired):
        self._conn = conn
        self.acquired = acquired

    def release(self):
        if self._conn is not None:
            try:
                self._conn.close()  # disconnecting releases the session lock
            except Exception:
                pass
            self._conn = None


def acquire_scheduler_lock(name: str, dsn: str = None) -> SchedulerLockLease:
    """Try to become the cross-task singleton runner for a scheduled tick.

    Returns a SchedulerLockLease; ``acquired`` False means another task holds
    the lock (or the lock service was unreachable) and this tick must be
    skipped. Without PostgreSQL (single-process dev/test) the lease is always
    acquired, with no connection held.
    """
    key = SCHEDULER_LOCK_KEYS[name]  # unknown name = programming error, loud
    dsn = dsn or (DATABASE_URL if USE_POSTGRESQL else None)
    if not dsn:
        return SchedulerLockLease(None, True)
    conn = None
    try:
        # connect_timeout is deliberately short (3s, not the pool's 10s): this
        # runs inline on the Tornado IOLoop from the PeriodicCallback wrapper,
        # so a slow/unreachable PG must make the tick SKIP FAST (fail-closed)
        # rather than stall the event loop — which matches the lease's
        # skip-on-failure contract. Moving the whole tick (this connect AND the
        # already-synchronous tick body's get_db/queries) off the IOLoop is the
        # complete fix and belongs to B7/PR-12 (non-blocking I/O), not here.
        # TCP keepalives: the lock is only as strong as this session. If the
        # socket died silently (RDS failover/partition), PG would release the
        # lock server-side while the tick still runs — keepalives bound that
        # window to ~60s instead of the OS default of hours.
        conn = psycopg2.connect(
            dsn, sslmode="require", connect_timeout=3,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            acquired = bool(cur.fetchone()[0])
        if not acquired:
            conn.close()
            return SchedulerLockLease(None, False)
        return SchedulerLockLease(conn, True)
    except Exception as e:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        # Skip rather than run unlocked: if the lock connection failed, the
        # database is almost certainly unreachable and the tick would fail
        # anyway — and running unlocked reintroduces the duplicate-run bug.
        logger.error(f"scheduler-lock '{name}': acquisition failed — skipping this tick: {e}")
        return SchedulerLockLease(None, False)


# ============================================================================
# Connection Wrapper Classes
# ============================================================================

class DBConnection:
    """
    Database connection wrapper that abstracts SQL dialect differences.
    Handles placeholder translation (? for SQLite, %s for PostgreSQL).
    """

    def __init__(self, conn, is_postgres: bool = False, database_identity: str = None):
        self.conn = conn
        self.is_postgres = is_postgres
        self.database_identity = database_identity
        if not is_postgres and database_identity is None:
            # Legacy tests and a few focused helpers construct DBConnection
            # directly.  Derive the SQLite identity from the connection so a
            # verified in-memory/temp test DB receives the narrow teardown
            # bypass without trusting ENVIRONMENT alone.
            try:
                database_rows = conn.execute("PRAGMA database_list").fetchall()
                main_path = next(
                    (row[2] for row in database_rows if len(row) > 1 and row[1] == "main"),
                    "",
                )
                self.database_identity = main_path or ":memory:"
            except Exception:
                self.database_identity = None
        self._cursor = None
        self._closed = False

    def _translate_query(self, sql: str) -> str:
        """
        Translate SQLite-specific SQL syntax to PostgreSQL equivalents.
        Handles: placeholders, datetime functions, INSERT OR variants, boolean literals.
        """
        if not self.is_postgres:
            return sql
        # 1. Placeholders: ? -> %s
        sql = sql.replace('?', '%s')
        # 2. Datetime: datetime('now') -> NOW(), date('now') -> CURRENT_DATE
        sql = sql.replace("datetime('now')", "NOW()")
        sql = sql.replace("date('now')", "CURRENT_DATE")
        # 2a. strftime('%Y-%m', col) -> to_char(col, 'YYYY-MM')  (SQLite→PostgreSQL)
        import re
        sql = re.sub(
            r"strftime\(\s*'%Y-%m'\s*,\s*([^)]+)\)",
            r"to_char(\1, 'YYYY-MM')",
            sql
        )
        # 2b. rowid -> id (rowid is SQLite-specific)
        sql = sql.replace("ORDER BY rowid", "ORDER BY id")
        # 2c. AUTOINCREMENT -> SERIAL (SQLite vs PostgreSQL auto-increment)
        if "AUTOINCREMENT" in sql.upper():
            import re
            sql = re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE)
        # 3. INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
        #    Pattern: INSERT OR IGNORE INTO table (...) VALUES (...)
        if "INSERT OR IGNORE" in sql.upper():
            sql = sql.replace("INSERT OR IGNORE", "INSERT")
            sql = sql.replace("insert or ignore", "INSERT")
            # Append ON CONFLICT DO NOTHING before any trailing semicolon
            sql = sql.rstrip().rstrip(';')
            sql += " ON CONFLICT DO NOTHING"
        # 4. INSERT OR REPLACE -> INSERT ... ON CONFLICT (...) DO UPDATE
        #    For simple cases, convert to PostgreSQL upsert.
        #    This requires knowing the conflict column — use id or primary key.
        if "INSERT OR REPLACE" in sql.upper():
            sql = sql.replace("INSERT OR REPLACE", "INSERT")
            sql = sql.replace("insert or replace", "INSERT")
            # For PostgreSQL, INSERT OR REPLACE semantics need ON CONFLICT.
            # Since our tables use 'id' as PK, we use ON CONFLICT (id) DO UPDATE.
            # Extract column names from the INSERT statement for the DO UPDATE SET clause.
            import re
            col_match = re.search(r'\(([^)]+)\)\s*VALUES', sql, re.IGNORECASE)
            if col_match:
                cols = [c.strip() for c in col_match.group(1).split(',')]
                # Build SET clause excluding the primary key (first column = id)
                set_parts = [f"{c} = EXCLUDED.{c}" for c in cols[1:] if c.lower() != 'id']
                if set_parts:
                    sql = sql.rstrip().rstrip(';')
                    sql += f" ON CONFLICT ({cols[0]}) DO UPDATE SET " + ", ".join(set_parts)
                else:
                    sql = sql.rstrip().rstrip(';')
                    sql += f" ON CONFLICT ({cols[0]}) DO NOTHING"
        # 5. Boolean: SQLite uses 0/1, but psycopg2 handles Python bool->PG bool natively
        #    No SQL text translation needed — handled at parameter level.
        return sql

    def _cursor_or_create(self):
        """Get or create a cursor."""
        if self._cursor is None:
            if self.is_postgres:
                self._cursor = self.conn.cursor(cursor_factory=RealDictCursor)
            else:
                self.conn.row_factory = sqlite3.Row
                self._cursor = self.conn.cursor()
        return self._cursor

    def execute(self, sql: str, params: Tuple = ()) -> 'DBConnection':
        """Execute SQL query with automatic dialect translation."""
        from regulated_deletion import assert_sql_delete_allowed
        assert_sql_delete_allowed(
            sql,
            database_identity=self.database_identity,
            is_postgres=self.is_postgres,
        )
        cursor = self._cursor_or_create()
        sql = self._translate_query(sql)
        try:
            cursor.execute(sql, params)
        except Exception as e:
            if self.is_postgres:
                # PostgreSQL requires rollback after any error to continue using the connection
                try:
                    self.conn.rollback()
                except Exception:
                    pass
            raise
        return self

    def executescript(self, sql: str) -> None:
        """Execute multiple SQL statements. Handles dialect differences.

        On PostgreSQL the script is run through ``_translate_query`` first so
        that file-based migrations authored in the repo's SQLite-portable
        convention (``INTEGER PRIMARY KEY AUTOINCREMENT``,
        ``DEFAULT (datetime('now'))``, etc.) execute cleanly. The inline
        ``_get_postgres_schema()`` DDL is already PostgreSQL-native; the
        translator is a no-op against constructs PG already accepts, so the
        translation is safe for both call sites.
        """
        from regulated_deletion import assert_sql_delete_allowed
        assert_sql_delete_allowed(
            sql,
            database_identity=self.database_identity,
            is_postgres=self.is_postgres,
        )
        if self.is_postgres:
            cursor = self._cursor_or_create()
            cursor.execute(self._translate_query(sql))
        elif "ADD COLUMN IF NOT EXISTS" in self._strip_sql_line_comments(sql).upper():
            self._execute_sqlite_script_with_add_column_if_not_exists(sql)
        else:
            self.conn.executescript(sql)

    @staticmethod
    def _strip_sql_line_comments(sql: str) -> str:
        return "\n".join(
            line for line in sql.splitlines()
            if not line.strip().startswith("--")
        )

    @staticmethod
    def _split_sql_statements(sql: str) -> List[str]:
        statements: List[str] = []
        current: List[str] = []
        in_single_quote = False
        in_double_quote = False
        i = 0
        while i < len(sql):
            char = sql[i]
            current.append(char)

            if in_single_quote:
                if char == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    current.append(sql[i + 1])
                    i += 2
                    continue
                if char == "'":
                    in_single_quote = False
            elif in_double_quote:
                if char == '"' and i + 1 < len(sql) and sql[i + 1] == '"':
                    current.append(sql[i + 1])
                    i += 2
                    continue
                if char == '"':
                    in_double_quote = False
            elif char == "'":
                in_single_quote = True
            elif char == '"':
                in_double_quote = True
            elif char == ";":
                current.pop()
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []

            i += 1

        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        return statements

    def _execute_sqlite_script_with_add_column_if_not_exists(self, sql: str) -> None:
        """Execute scripts using ALTER TABLE ADD COLUMN IF NOT EXISTS on SQLite.

        SQLite does not support that syntax, but file migrations need an
        idempotent way to be safe after init_db-created schemas. This shim only
        handles the exact additive ALTER pattern; all other statements execute
        normally after stripping leading SQL line comments.
        """
        cursor = self._cursor_or_create()
        pattern = re.compile(
            r"^ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+"
            r"IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$",
            re.IGNORECASE | re.DOTALL,
        )
        for statement in self._split_sql_statements(self._strip_sql_line_comments(sql)):
            match = pattern.match(statement)
            if match:
                table, column, definition = match.groups()
                columns = {
                    str(row["name"]).lower()
                    for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if column.lower() in columns:
                    continue
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition.strip()}")
                continue
            cursor.execute(statement)

    def fetchone(self):
        """Fetch single row. Returns sqlite3.Row (SQLite) or dict (PostgreSQL)."""
        cursor = self._cursor_or_create()
        row = cursor.fetchone()
        if row is None:
            return None
        if self.is_postgres:
            return dict(row)
        return dict(row)  # Convert sqlite3.Row to dict for consistent .get() support

    def fetchall(self):
        """Fetch all rows. Returns list of dict."""
        cursor = self._cursor_or_create()
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    @property
    def lastrowid(self):
        """Return the row id of the last INSERT executed on this connection.

        ``DBConnection.execute()`` returns ``self`` rather than a raw cursor
        so that callers can chain ``.fetchone()`` / ``.fetchall()``.  Some
        persistence helpers (e.g. ``screening_storage.persist_normalized_report``)
        call ``cursor.lastrowid`` on the object returned by ``execute()``.
        This property makes that pattern work correctly with the wrapper.
        """
        if self._cursor is not None:
            return self._cursor.lastrowid
        return None

    def commit(self) -> None:
        """Commit transaction."""
        self.conn.commit()

    def rollback(self) -> None:
        """Rollback transaction."""
        self.conn.rollback()

    def close(self) -> None:
        """Close connection and return to pool if PostgreSQL."""
        if self._closed:
            return
        self._closed = True
        if self._cursor:
            try:
                self._cursor.close()
            except Exception:
                pass
            self._cursor = None
        if self.is_postgres:
            # Never return a failed transaction to the pool.  Rollback after a
            # prior commit is harmless; rollback after an exception clears the
            # aborted transaction state before the next borrower receives it.
            try:
                self.conn.rollback()
            except Exception:
                pass
            # Return connection to pool
            if _pg_pool:
                _pg_pool.putconn(self.conn)
            else:
                self.conn.close()
        else:
            self.conn.close()


# ============================================================================
# Main Database Interface
# ============================================================================

def _checkout_validated_pg_conn(max_attempts: int = 3):
    """Borrow a PostgreSQL connection from the pool and validate it before use
    (DCI-007 pre-ping).

    A pooled connection can go stale after an RDS failover, network blip, or
    idle-timeout — psycopg2's ThreadedConnectionPool does NOT check liveness on
    checkout, so a dead connection would be handed to a request handler and fail
    on its first statement. Here we run a lightweight ``SELECT 1``; if it fails
    the connection is discarded from the pool (``putconn(close=True)``) and a
    fresh one is fetched, up to ``max_attempts`` times. This turns a
    once-per-failover request error into a transparent retry.
    """
    last_exc = None
    for _attempt in range(max_attempts):
        conn = _pg_pool.getconn()
        try:
            # Any leftover aborted-transaction state on a reused connection would
            # make SELECT 1 raise; rolling back first makes the ping a true
            # liveness check rather than a transaction-state check.
            conn.rollback()
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                cur.close()
            return conn
        except Exception as exc:  # noqa: BLE001 — any failure means discard+retry
            last_exc = exc
            logger.warning(
                "Stale PostgreSQL connection discarded on checkout (attempt %d/%d): %s",
                _attempt + 1, max_attempts, exc,
            )
            try:
                _pg_pool.putconn(conn, close=True)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
    raise RuntimeError(
        f"Could not obtain a live PostgreSQL connection after {max_attempts} "
        f"attempts: {last_exc}"
    )


def get_db() -> DBConnection:
    """
    Get a database connection.
    - For PostgreSQL: returns a validated (pre-pinged) connection from the pool
    - For SQLite: returns a new connection

    C-07: SQLite is BLOCKED in production. Production MUST use PostgreSQL.
    """
    env = _CFG_ENVIRONMENT.lower()

    if USE_POSTGRESQL:
        init_pg_pool()
        conn = _checkout_validated_pg_conn()
        return DBConnection(conn, is_postgres=True, database_identity=DATABASE_URL)
    else:
        # C-07: Block SQLite in production — this is a CRITICAL safety guard
        if env in ("production", "prod"):
            raise RuntimeError(
                "CRITICAL: SQLite is FORBIDDEN in production. "
                "Set DATABASE_URL to a PostgreSQL connection string. "
                "Example: DATABASE_URL=postgresql://user:pass@host:5432/arie_production"
            )
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return DBConnection(conn, is_postgres=False, database_identity=DB_PATH)


# ============================================================================
# Schema Creation
# ============================================================================

def _get_postgres_schema() -> str:
    """PostgreSQL-compatible schema with necessary extensions and data types."""
    return """
    -- Enable required extensions
    CREATE EXTENSION IF NOT EXISTS pgcrypto;

    -- Users / Officers
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'analyst' CHECK(role IN ('admin','sco','co','analyst')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Client accounts (applicants)
    CREATE TABLE IF NOT EXISTS clients (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        company_name TEXT,
        -- P12-5 / DCI-006: CLIENT_STATUS_VALUES (kept in lockstep by test)
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
        password_reset_token TEXT,
        password_reset_expires TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Applications
    CREATE TABLE IF NOT EXISTS applications (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        ref TEXT UNIQUE NOT NULL,
        client_id TEXT REFERENCES clients(id),
        company_name TEXT NOT NULL,
        brn TEXT,
        country TEXT,
        sector TEXT,
        entity_type TEXT,
        ownership_structure TEXT,
        prescreening_data JSONB DEFAULT '{}',
        risk_score REAL,
        risk_level TEXT CHECK(risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_dimensions JSONB DEFAULT '{}',
        onboarding_lane TEXT,
        status TEXT DEFAULT 'draft' CHECK(status IN (
            'draft','submitted','prescreening_submitted','pricing_review','pricing_accepted',
            'pre_approval_review','pre_approved',
            'kyc_documents','kyc_submitted','compliance_review','submitted_to_compliance','in_review','under_review',
            'edd_required','approved','rejected','rmi_sent','withdrawn'
        )),
        assigned_to TEXT REFERENCES users(id),
        submitted_at TIMESTAMP,
        decided_at TIMESTAMP,
        decision_by TEXT REFERENCES users(id),
        decision_notes TEXT,
        pre_approval_decision TEXT,
        pre_approval_notes TEXT,
        pre_approval_officer_id TEXT REFERENCES users(id),
        pre_approval_timestamp TIMESTAMP,
        screening_mode TEXT DEFAULT 'live',
        is_fixture BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        inputs_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Directors
    CREATE TABLE IF NOT EXISTS directors (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT NOT NULL,
        nationality TEXT,
        is_pep BOOLEAN DEFAULT false,
        pep_declaration JSONB DEFAULT '{}',
        date_of_birth TEXT,
        country_of_residence TEXT,
        residential_address TEXT,
        date_of_appointment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- UBOs
    CREATE TABLE IF NOT EXISTS ubos (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT NOT NULL,
        nationality TEXT,
        ownership_pct REAL,
        is_pep BOOLEAN DEFAULT false,
        pep_declaration JSONB DEFAULT '{}',
        date_of_birth TEXT,
        country_of_residence TEXT,
        residential_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Intermediary shareholders
    CREATE TABLE IF NOT EXISTS intermediaries (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        entity_name TEXT NOT NULL,
        jurisdiction TEXT,
        registration_number TEXT,
        registered_address TEXT,
        ownership_pct REAL,
        owned_or_controlled_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Documents
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_id TEXT,
        doc_type TEXT NOT NULL,
        doc_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        s3_key TEXT,
        file_size INTEGER,
        mime_type TEXT,
        file_sha256 TEXT,
        slot_key TEXT,
        is_current BOOLEAN DEFAULT TRUE,
        version INTEGER DEFAULT 1,
        superseded_at TIMESTAMP,
        superseded_by_document_id TEXT REFERENCES documents(id),
        replaced_reason TEXT,
        replaced_by_user_id TEXT,
        expiry_date TIMESTAMP,
        valid_until TIMESTAMP,
        expiry_source TEXT,
        expiry_confidence REAL,
        expiry_extracted_at TIMESTAMP,
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','in_progress','verified','flagged','failed','skipped')),
        verification_results JSONB DEFAULT '{}',
        review_status TEXT DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','info_requested')),
        review_comment TEXT,
        reviewed_by TEXT REFERENCES users(id),
        evidence_class TEXT,
        evidence_classification_note TEXT,
        evidence_classified_by TEXT REFERENCES users(id),
        evidence_classified_at TIMESTAMP,
        workflow_test_accepted BOOLEAN DEFAULT FALSE,
        workflow_test_acceptance_reason TEXT,
        workflow_test_accepted_by TEXT REFERENCES users(id),
        workflow_test_accepted_at TIMESTAMP,
        workflow_test_acceptance_environment TEXT,
        uploaded_by TEXT REFERENCES users(id),
        uploaded_by_actor_type TEXT,
        uploaded_by_actor_id TEXT,
        uploaded_by_display TEXT,
        upload_source TEXT,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        verified_at TIMESTAMP,
        reviewed_at TIMESTAMP
    );

    -- Async verification jobs (dark behind FF_ASYNC_VERIFY)
    CREATE TABLE IF NOT EXISTS verification_jobs (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
        priority INTEGER NOT NULL DEFAULT 100,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        locked_by TEXT,
        locked_at TIMESTAMP,
        last_error TEXT,
        job_metadata JSONB DEFAULT '{}',
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_verification_jobs_status_run_after
        ON verification_jobs(status, run_after, priority, created_at);
    CREATE INDEX IF NOT EXISTS idx_verification_jobs_document
        ON verification_jobs(document_id, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_verification_jobs_active_document
        ON verification_jobs(document_id)
        WHERE status IN ('pending','retrying','in_progress');

    -- Async screening jobs for submit-time provider work.
    CREATE TABLE IF NOT EXISTS screening_jobs (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        submit_attempt_id TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'complyadvantage',
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
        priority INTEGER NOT NULL DEFAULT 100,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        locked_by TEXT,
        locked_at TIMESTAMP,
        last_error TEXT,
        job_metadata JSONB DEFAULT '{}',
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_screening_jobs_status_run_after
        ON screening_jobs(status, run_after, priority, created_at);
    CREATE INDEX IF NOT EXISTS idx_screening_jobs_application
        ON screening_jobs(application_id, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_jobs_active_application
        ON screening_jobs(application_id)
        WHERE status IN ('pending','retrying','in_progress');

    -- Compliance Resources
    CREATE TABLE IF NOT EXISTS compliance_resources (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        slug TEXT UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        category TEXT DEFAULT 'internal',
        resource_type TEXT DEFAULT 'uploaded' CHECK(resource_type IN ('system','uploaded')),
        file_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        s3_key TEXT,
        mime_type TEXT,
        file_size INTEGER,
        uploaded_by TEXT REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Regulatory Intelligence Documents
    CREATE TABLE IF NOT EXISTS regulatory_documents (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        title TEXT NOT NULL,
        regulator TEXT NOT NULL,
        jurisdiction TEXT NOT NULL,
        doc_type TEXT NOT NULL,
        publication_date TEXT,
        effective_date TEXT,
        file_name TEXT,
        file_path TEXT,
        s3_key TEXT,
        mime_type TEXT,
        file_size INTEGER,
        source_text TEXT,
        status TEXT DEFAULT 'uploaded' CHECK(status IN ('uploaded','analysed','review_required','analysis_failed')),
        analysis_source TEXT,
        analysis_summary JSONB DEFAULT '{}',
        audit_trail JSONB DEFAULT '[]',
        uploaded_by TEXT REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Risk Model Configuration
    CREATE TABLE IF NOT EXISTS risk_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        dimensions JSONB NOT NULL DEFAULT '{}',
        thresholds JSONB NOT NULL DEFAULT '{}',
        country_risk_scores JSONB DEFAULT '{}',
        sector_risk_scores JSONB DEFAULT '{}',
        entity_type_scores JSONB DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by TEXT REFERENCES users(id)
    );

    -- PR-CR1: Canonical country-risk source governance snapshots
    CREATE TABLE IF NOT EXISTS country_risk_snapshots (
        id TEXT PRIMARY KEY,
        version TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        source_name TEXT NOT NULL,
        source_url TEXT,
        source_publication_date TEXT,
        effective_date TEXT NOT NULL,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        imported_by TEXT NOT NULL DEFAULT 'system',
        last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        checksum TEXT NOT NULL,
        freshness_days INTEGER NOT NULL DEFAULT 180,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS country_risk_entries (
        id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL REFERENCES country_risk_snapshots(id),
        country_name TEXT NOT NULL,
        country_key TEXT NOT NULL,
        iso_alpha2 TEXT,
        iso_alpha3 TEXT,
        risk_rating TEXT NOT NULL,
        risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 1 AND 4),
        fatf_status TEXT NOT NULL DEFAULT 'none',
        sanctions_status TEXT NOT NULL DEFAULT 'none',
        high_risk_status TEXT NOT NULL DEFAULT 'none',
        source_name TEXT NOT NULL,
        source_url TEXT,
        source_publication_date TEXT,
        effective_date TEXT NOT NULL,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        imported_by TEXT NOT NULL DEFAULT 'system',
        status TEXT NOT NULL DEFAULT 'active',
        checksum TEXT NOT NULL,
        notes TEXT,
        previous_risk_rating TEXT,
        previous_fatf_status TEXT,
        UNIQUE(snapshot_id, country_key)
    );

    CREATE INDEX IF NOT EXISTS idx_country_risk_entries_lookup
        ON country_risk_entries(snapshot_id, country_key, status);

    CREATE INDEX IF NOT EXISTS idx_country_risk_entries_fatf
        ON country_risk_entries(fatf_status, status);

    -- System Settings
    CREATE TABLE IF NOT EXISTS system_settings (
        id INTEGER PRIMARY KEY DEFAULT 1,
        company_name TEXT NOT NULL DEFAULT 'Onboarda Ltd',
        licence_number TEXT DEFAULT 'FSC-PIS-2024-001',
        default_retention_years INTEGER NOT NULL DEFAULT 7,
        auto_approve_max_score INTEGER NOT NULL DEFAULT 40,
        edd_threshold_score INTEGER NOT NULL DEFAULT 55,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by TEXT REFERENCES users(id)
    );

    -- Enhanced / EDD requirement settings
    CREATE TABLE IF NOT EXISTS enhanced_requirement_rules (
        id SERIAL PRIMARY KEY,
        trigger_key TEXT NOT NULL,
        trigger_label TEXT NOT NULL,
        trigger_category TEXT NOT NULL DEFAULT 'risk',
        requirement_key TEXT NOT NULL,
        requirement_label TEXT NOT NULL,
        requirement_description TEXT,
        audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
        requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
        subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
        blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
        waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
        waiver_roles JSONB DEFAULT '[]',
        mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        sort_order INTEGER NOT NULL DEFAULT 100,
        applies_when JSONB DEFAULT '{}',
        client_safe_label TEXT,
        client_safe_description TEXT,
        internal_notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by TEXT REFERENCES users(id),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by TEXT REFERENCES users(id),
        UNIQUE(trigger_key, requirement_key)
    );
    CREATE INDEX IF NOT EXISTS idx_enhanced_req_trigger ON enhanced_requirement_rules(trigger_key);
    CREATE INDEX IF NOT EXISTS idx_enhanced_req_active ON enhanced_requirement_rules(active);
    CREATE INDEX IF NOT EXISTS idx_enhanced_req_audience ON enhanced_requirement_rules(audience);

    -- Application-specific generated Enhanced / EDD requirements
    CREATE TABLE IF NOT EXISTS application_enhanced_requirements (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        source_rule_id INTEGER REFERENCES enhanced_requirement_rules(id),
        trigger_key TEXT NOT NULL,
        trigger_label TEXT NOT NULL,
        trigger_category TEXT NOT NULL DEFAULT 'risk',
        requirement_key TEXT NOT NULL,
        requirement_label TEXT NOT NULL,
        requirement_description TEXT,
        audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
        requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
        subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
        blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
        waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
        waiver_roles TEXT DEFAULT '[]',
        mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
        status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN ('generated','requested','uploaded','under_review','accepted','rejected','waived','cancelled')),
        generation_source TEXT NOT NULL DEFAULT 'manual_api',
        trigger_reason TEXT,
        trigger_context TEXT DEFAULT '{}',
        linked_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
        monitoring_alert_id INTEGER,
        monitoring_document_id TEXT,
        due_date TIMESTAMP,
        linked_rmi_item_id TEXT,
        requested_at TIMESTAMP,
        requested_by TEXT REFERENCES users(id),
        uploaded_at TIMESTAMP,
        client_response_text TEXT,
        client_response_at TIMESTAMP,
        client_response_by TEXT REFERENCES clients(id),
        reviewed_at TIMESTAMP,
        reviewed_by TEXT REFERENCES users(id),
        review_notes TEXT,
        waived_at TIMESTAMP,
        waived_by TEXT REFERENCES users(id),
        waiver_reason TEXT,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by TEXT REFERENCES users(id),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by TEXT REFERENCES users(id),
        UNIQUE(application_id, trigger_key, requirement_key)
    );
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_app ON application_enhanced_requirements(application_id);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_rule ON application_enhanced_requirements(source_rule_id);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_trigger ON application_enhanced_requirements(trigger_key);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_status ON application_enhanced_requirements(status);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_active ON application_enhanced_requirements(active);

    -- AI Agents Configuration
    CREATE TABLE IF NOT EXISTS ai_agents (
        id SERIAL PRIMARY KEY,
        agent_number INTEGER UNIQUE NOT NULL,
        name TEXT NOT NULL,
        icon TEXT DEFAULT '🤖',
        stage TEXT NOT NULL,
        description TEXT,
        enabled BOOLEAN DEFAULT true,
        checks JSONB DEFAULT '[]',
        supervisor_agent_type TEXT,
        risk_dimensions JSONB DEFAULT '[]',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- AI Verification Checks Configuration
    CREATE TABLE IF NOT EXISTS ai_checks (
        id SERIAL PRIMARY KEY,
        category TEXT NOT NULL CHECK(category IN ('entity','person')),
        doc_type TEXT NOT NULL,
        doc_name TEXT NOT NULL,
        checks JSONB DEFAULT '[]',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Agent Execution Traceability Log
    CREATE TABLE IF NOT EXISTS agent_executions (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL,
        document_id TEXT,
        agent_name TEXT NOT NULL,
        agent_number INTEGER,
        -- P12-5 / DCI-006: AGENT_EXECUTION_STATUS_VALUES (lockstep test)
        status TEXT NOT NULL CHECK(status IN (
            'verified','flagged','skipped','completed',
            'pending','in_progress','failed','error'
        )),
        checks_json JSONB,
        flags_json JSONB,
        requires_review BOOLEAN DEFAULT false,
        -- P12-5 / DCI-006: AGENT_EXECUTION_SOURCE_VALUES (lockstep test)
        source TEXT DEFAULT 'ai' CHECK(source IN ('ai','stored_screening_results')),
        started_at TIMESTAMP,
        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        error_message TEXT
    );

    -- Screening Review Dispositions
    CREATE TABLE IF NOT EXISTS screening_reviews (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        subject_type TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN ('cleared','escalated','follow_up_required')),
        notes TEXT,
        disposition_code TEXT,
        rationale TEXT,
        sensitivity_flags TEXT DEFAULT '[]',
        requires_four_eyes BOOLEAN DEFAULT false,
        reviewer_id TEXT REFERENCES users(id),
        reviewer_name TEXT,
        second_reviewer_id TEXT REFERENCES users(id),
        second_reviewer_name TEXT,
        second_disposition_code TEXT,
        second_rationale TEXT,
        second_reviewed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(application_id, subject_type, subject_name)
    );

    -- SRP per-hit disposition: each individual screening hit is dispositioned on
    -- its own (Confirm true match / Clear false positive / Escalate / Request
    -- more information) with a per-hit materiality call recorded on a true match.
    -- The subject-level rollup that feeds the frozen approval gates is still
    -- written through screening_reviews (via the existing /screening/review
    -- flow) — this table is the granular per-hit record backing the review UI and
    -- the audit trail. hit_id is the stable provider record identifier. An "undo"
    -- deletes the row, so 'pending' is never stored.
    CREATE TABLE IF NOT EXISTS screening_hit_dispositions (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        subject_type TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        hit_id TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN ('match','cleared','escalated','follow_up_required')),
        materiality TEXT CHECK(materiality IN ('high','moderate','nonmaterial','insufficient')),
        rationale TEXT,
        reviewer_id TEXT REFERENCES users(id),
        reviewer_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(application_id, subject_type, subject_name, hit_id)
    );

    -- SRP-2: superseded screening-report snapshots. A governed re-screen
    -- archives the outgoing report here before replacement — screening
    -- evidence is regulated and is never destroyed by a refresh.
    CREATE TABLE IF NOT EXISTS screening_report_archive (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL,
        application_ref TEXT,
        archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        archived_by TEXT NOT NULL,
        reason TEXT NOT NULL,
        report_hash TEXT NOT NULL,
        report_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_screening_report_archive_app
        ON screening_report_archive(application_id);

    -- Audit Trail
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_id TEXT,
        user_name TEXT,
        user_role TEXT,
        action TEXT NOT NULL,
        target TEXT,
        application_id TEXT,
        detail TEXT,
        ip_address TEXT,
        -- P12-9 / DCI-028: request correlation id (nullable; chain-safe)
        request_id TEXT
    );

    -- Notifications
    CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        user_id TEXT REFERENCES users(id),
        title TEXT NOT NULL,
        message TEXT,
        read BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Session / Save & Resume tokens
    CREATE TABLE IF NOT EXISTS client_sessions (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        client_id TEXT REFERENCES clients(id),
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        form_data JSONB DEFAULT '{}',
        last_step INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Monitoring Alerts
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        id SERIAL PRIMARY KEY,
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        provider TEXT,
        case_identifier TEXT,
        discovered_via TEXT NOT NULL DEFAULT 'webhook_live'
            CHECK(discovered_via IN ('webhook_live','webhook_backfill','manual_backfill','manual','officer_created','document_health')),
        discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        backfill_run_id TEXT,
        client_name TEXT,
        alert_type TEXT,
        severity TEXT,
        detected_by TEXT,
        summary TEXT,
        source_reference TEXT,
        ai_recommendation TEXT,
        status TEXT DEFAULT 'open',
        officer_action TEXT,
        officer_notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TIMESTAMP,
        reviewed_by TEXT REFERENCES users(id),
        linked_periodic_review_id INTEGER,
        linked_edd_case_id INTEGER,
        triaged_at TIMESTAMP,
        assigned_at TIMESTAMP,
        resolved_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS complyadvantage_webhook_deliveries (
        webhook_id TEXT PRIMARY KEY,
        first_received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        duplicate_count INTEGER NOT NULL DEFAULT 0,
        webhook_type TEXT,
        case_identifier TEXT,
        customer_identifier TEXT,
        processing_status TEXT NOT NULL DEFAULT 'processing',
        processing_result TEXT,
        failure_reason TEXT,
        trace_id TEXT,
        payload_json TEXT,
        alert_identifiers_json TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMP,
        processed_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS monitoring_alert_evidence (
        id SERIAL PRIMARY KEY,
        monitoring_alert_id INTEGER NOT NULL,
        application_id TEXT,
        provider TEXT NOT NULL,
        case_identifier TEXT,
        alert_identifier TEXT,
        match_identifier TEXT,
        risk_identifier TEXT,
        profile_identifier TEXT,
        evidence_type TEXT,
        matched_subject_name TEXT,
        relationship_to_client TEXT,
        match_category TEXT,
        risk_indicator TEXT,
        match_confidence TEXT,
        source_title TEXT,
        source_name TEXT,
        source_url TEXT,
        source_url_available BOOLEAN DEFAULT false,
        source_url_unavailable_reason TEXT,
        publication_date TEXT,
        snippet TEXT,
        provider_case_url TEXT,
        evidence_json TEXT,
        raw_provider_reference TEXT,
        evidence_status TEXT DEFAULT 'fetched',
        evidence_hash TEXT NOT NULL,
        fetched_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(monitoring_alert_id, evidence_hash)
    );

    -- Periodic Reviews
    CREATE TABLE IF NOT EXISTS periodic_reviews (
        id SERIAL PRIMARY KEY,
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        client_name TEXT,
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        last_review_date TEXT,
        next_review_date TEXT,
        trigger_type TEXT,
        trigger_reason TEXT,
        trigger_source TEXT,
        linked_monitoring_alert_id INTEGER,
        linked_edd_case_id INTEGER,
        review_reason TEXT,
        previous_risk_level TEXT CHECK(previous_risk_level IS NULL OR previous_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        new_risk_level TEXT CHECK(new_risk_level IS NULL OR new_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        review_memo TEXT,
        status TEXT DEFAULT 'pending',
        due_date TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        assigned_officer TEXT REFERENCES users(id),
        assigned_by TEXT REFERENCES users(id),
        assigned_at TIMESTAMP,
        reassigned_reason TEXT,
        closed_at TIMESTAMP,
        sla_due_at TIMESTAMP,
        priority TEXT,
        decision TEXT,
        decision_reason TEXT,
        outcome TEXT,
        outcome_reason TEXT,
        outcome_recorded_at TIMESTAMP,
        review_cycle_number INTEGER DEFAULT 1,
        review_type TEXT,
        policy_version TEXT,
        frequency_months INTEGER,
        calculation_basis TEXT,
        legacy_import BOOLEAN DEFAULT FALSE,
        legacy_source_type TEXT,
        legacy_source_note TEXT,
        legacy_review_evidence_note TEXT,
        legacy_confidence TEXT,
        legacy_entered_by TEXT REFERENCES users(id),
        legacy_entered_at TIMESTAMP,
        legacy_sco_acknowledged_by TEXT REFERENCES users(id),
        legacy_sco_acknowledged_at TIMESTAMP,
        import_requires_ack BOOLEAN DEFAULT FALSE,
        baseline_status TEXT,
        baseline_date TEXT,
        baseline_cadence_months INTEGER,
        baseline_note TEXT,
        material_change_attestation TEXT,
        material_change_categories JSONB DEFAULT '[]',
        risk_change_attestation TEXT,
        risk_rerate_reason TEXT,
        risk_rerated_by TEXT REFERENCES users(id),
        risk_rerated_at TIMESTAMP,
        client_attestation_status TEXT DEFAULT 'not_started',
        client_attestation_payload JSONB DEFAULT '{}',
        client_attestation_saved_at TIMESTAMP,
        client_attestation_submitted_at TIMESTAMP,
        client_attestation_submitted_by TEXT REFERENCES clients(id),
        client_attestation_questionnaire_version TEXT,
        officer_rationale TEXT,
        officer_findings_note TEXT,
        officer_deficiencies_note TEXT,
        officer_internal_review_note TEXT,
        findings_updated_by TEXT REFERENCES users(id),
        findings_updated_at TIMESTAMP,
        memo_status TEXT,
        periodic_review_memo_id INTEGER,
        risk_reassessment_status TEXT DEFAULT 'not_started',
        risk_impact_category TEXT,
        officer_risk_decision TEXT,
        confirmed_risk_level TEXT CHECK(confirmed_risk_level IS NULL OR confirmed_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_reassessment_rationale TEXT,
        risk_reassessment_saved_by TEXT REFERENCES users(id),
        risk_reassessment_saved_at TIMESTAMP,
        senior_review_required BOOLEAN DEFAULT FALSE,
        senior_review_reason TEXT,
        memo_addendum_status TEXT DEFAULT 'not_generated',
        memo_addendum_generated_at TIMESTAMP,
        memo_addendum_finalized_at TIMESTAMP,
        memo_addendum_finalized_by TEXT REFERENCES users(id),
        client_notification_status TEXT DEFAULT 'not_sent',
        initial_notification_sent_at TIMESTAMP,
        last_reminder_sent_at TIMESTAMP,
        reminder_count INTEGER DEFAULT 0,
        last_notification_error TEXT,
        officer_alert_status TEXT,
        officer_alerted_at TIMESTAMP,
        notification_channel TEXT DEFAULT 'portal',
        next_reminder_due_at TIMESTAMP,
        required_items TEXT,
        required_items_generated_at TIMESTAMP,
        state_changed_at TIMESTAMP,
        decided_by TEXT REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS periodic_review_memos (
        id SERIAL PRIMARY KEY,
        periodic_review_id INTEGER NOT NULL,
        application_id TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        memo_data TEXT NOT NULL,
        memo_context TEXT NOT NULL,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        generated_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'generated',
        UNIQUE(periodic_review_id, version)
    );
    CREATE INDEX IF NOT EXISTS idx_prm_review ON periodic_review_memos(periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_prm_app ON periodic_review_memos(application_id);

    CREATE TABLE IF NOT EXISTS periodic_review_evidence_links (
        id SERIAL PRIMARY KEY,
        periodic_review_id INTEGER NOT NULL REFERENCES periodic_reviews(id) ON DELETE CASCADE,
        requirement_id TEXT,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        link_type TEXT,
        linked_by TEXT REFERENCES users(id),
        linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        note TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_prev_links_review ON periodic_review_evidence_links(periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_prev_links_document ON periodic_review_evidence_links(document_id);

    -- Monitoring Agent Status
    CREATE TABLE IF NOT EXISTS monitoring_agent_status (
        id SERIAL PRIMARY KEY,
        agent_name TEXT,
        agent_type TEXT,
        last_run TIMESTAMP,
        next_run TIMESTAMP,
        run_frequency TEXT,
        clients_monitored INTEGER,
        alerts_generated INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active'
    );

    -- M2.2 four-eyes: maker-checker review requests for material alert clears.
    -- Additive; the alert lifecycle status stays on monitoring_alerts. "Pending
    -- second review" is derived from an open row here, never a stored status.
    CREATE TABLE IF NOT EXISTS monitoring_alert_review_requests (
        id SERIAL PRIMARY KEY,
        alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
        tier INTEGER,
        requested_outcome TEXT,
        dismissal_reason TEXT,
        rationale TEXT,
        evidence_ref TEXT,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','senior_cleared')),
        initiated_by TEXT,
        initiated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_by TEXT,
        approved_at TIMESTAMP,
        approval_note TEXT,
        rejection_reason TEXT,
        second_review_bypassed INTEGER DEFAULT 0,
        sampled_for_qa INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_monitoring_review_requests_alert
        ON monitoring_alert_review_requests(alert_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_review_requests_state
        ON monitoring_alert_review_requests(state);

    -- M2.1 PR-2: officer follow-up tracker for monitoring alerts. Additive
    -- annotation ledger; NEVER changes monitoring_alerts.status (aging/next-step
    -- are derived from these rows, not stored on the alert).
    CREATE TABLE IF NOT EXISTS monitoring_alert_followups (
        id SERIAL PRIMARY KEY,
        alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
        action TEXT NOT NULL DEFAULT 'note'
            CHECK(action IN ('note','next_step','snooze_until','contacted_client','pending_review','other')),
        note TEXT,
        due_at TIMESTAMP,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP,
        resolved_by TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_monitoring_followups_alert
        ON monitoring_alert_followups(alert_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_followups_open
        ON monitoring_alert_followups(alert_id, resolved_at);

    -- M2.1 PR-4: officer-triggered overdue escalation ledger. Additive
    -- metadata only; the canonical alert status still moves through the
    -- existing monitoring decision transition to monitoring_alerts.status =
    -- 'escalated'.
    CREATE TABLE IF NOT EXISTS monitoring_alert_escalations (
        id SERIAL PRIMARY KEY,
        alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
        reason TEXT NOT NULL,
        escalated_by TEXT,
        escalated_by_role TEXT,
        escalated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        prior_status TEXT,
        new_status TEXT,
        sla_state TEXT,
        days_overdue INTEGER,
        sla_due_at TIMESTAMP,
        sla_days INTEGER,
        alert_severity_at_escalation TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_monitoring_alert_escalations_alert
        ON monitoring_alert_escalations(alert_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alert_escalations_actor
        ON monitoring_alert_escalations(escalated_by);

    -- Client Notifications
    CREATE TABLE IF NOT EXISTS client_notifications (
        id SERIAL PRIMARY KEY,
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        client_id TEXT REFERENCES clients(id),
        notification_type TEXT,
        title TEXT NOT NULL,
        message TEXT,
        documents_list TEXT,
        rmi_request_id TEXT,
        read_status BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        read_at TIMESTAMP
    );

    -- Structured Request for More Information (RMI)
    CREATE TABLE IF NOT EXISTS rmi_requests (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        client_id TEXT REFERENCES clients(id),
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending_review','partially_fulfilled','fulfilled','cancelled')),
        reason TEXT NOT NULL,
        deadline TEXT NOT NULL,
        created_by TEXT REFERENCES users(id),
        created_by_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        fulfilled_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_rmi_requests_app ON rmi_requests(application_id);
    CREATE INDEX IF NOT EXISTS idx_rmi_requests_client ON rmi_requests(client_id);
    CREATE INDEX IF NOT EXISTS idx_rmi_requests_status ON rmi_requests(status);

    CREATE TABLE IF NOT EXISTS rmi_request_items (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL REFERENCES rmi_requests(id) ON DELETE CASCADE,
        doc_type TEXT NOT NULL,
        label TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','uploaded','accepted','rejected')),
        document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
        uploaded_at TIMESTAMP,
        reviewed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_rmi_items_request ON rmi_request_items(request_id);
    CREATE INDEX IF NOT EXISTS idx_rmi_items_doc ON rmi_request_items(document_id);

    CREATE TABLE IF NOT EXISTS idv_resolutions (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        application_ref TEXT,
        person_id TEXT,
        person_type TEXT,
        person_name TEXT,
        prior_provider_status TEXT,
        prior_review_answer TEXT,
        resolution_status TEXT NOT NULL,
        resolution_outcome TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        evidence_reviewed TEXT NOT NULL DEFAULT '[]',
        rationale TEXT NOT NULL,
        confirmation_text TEXT,
        senior_approver_id TEXT,
        resolved_by TEXT NOT NULL,
        resolved_by_name TEXT,
        resolved_by_role TEXT NOT NULL,
        ip_address TEXT,
        user_agent TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_idv_resolutions_app ON idv_resolutions(application_id);
    CREATE INDEX IF NOT EXISTS idx_idv_resolutions_subject ON idv_resolutions(application_id, person_type, person_id, person_name);
    CREATE INDEX IF NOT EXISTS idx_idv_resolutions_status ON idv_resolutions(resolution_status);

    -- Suspicious Activity Reports (SAR)
    CREATE TABLE IF NOT EXISTS sar_reports (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        alert_id INTEGER REFERENCES monitoring_alerts(id),
        sar_reference TEXT UNIQUE,
        report_type TEXT DEFAULT 'SAR' CHECK(report_type IN ('SAR','STR','CTR','MLRO')),
        subject_name TEXT NOT NULL,
        subject_type TEXT DEFAULT 'individual' CHECK(subject_type IN ('individual','entity')),
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        narrative TEXT NOT NULL,
        indicators JSONB DEFAULT '[]',
        transaction_details JSONB DEFAULT '{}',
        supporting_documents JSONB DEFAULT '[]',
        filing_status TEXT DEFAULT 'draft' CHECK(filing_status IN ('draft','pending_review','approved','filed','rejected','archived')),
        prepared_by TEXT REFERENCES users(id),
        reviewed_by TEXT REFERENCES users(id),
        approved_by TEXT REFERENCES users(id),
        filed_at TIMESTAMP,
        regulatory_body TEXT DEFAULT 'FIU Mauritius',
        external_reference TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Transaction Ledger (Agent 8: Behaviour & Risk Drift Detection)
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        transaction_ref TEXT,
        transaction_date TIMESTAMP NOT NULL,
        amount NUMERIC(18, 2) NOT NULL,
        currency TEXT DEFAULT 'USD',
        direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound','internal')),
        counterparty_name TEXT,
        counterparty_country TEXT,
        product_type TEXT,
        channel TEXT,
        description TEXT,
        risk_flags JSONB DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_transactions_application_id ON transactions(application_id);
    CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
    CREATE INDEX IF NOT EXISTS idx_transactions_counterparty_country ON transactions(counterparty_country);

    -- Enhanced Due Diligence (EDD) Cases
    CREATE TABLE IF NOT EXISTS edd_cases (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id),
        client_name TEXT NOT NULL,
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_score REAL,
        stage TEXT DEFAULT 'triggered' CHECK(stage IN ('triggered','information_gathering','analysis','pending_senior_review','edd_approved','edd_rejected')),
        assigned_officer TEXT REFERENCES users(id),
        senior_reviewer TEXT REFERENCES users(id),
        trigger_source TEXT DEFAULT 'officer_decision',
        trigger_notes TEXT,
        origin_context TEXT,
        linked_monitoring_alert_id INTEGER,
        linked_periodic_review_id INTEGER,
        assigned_at TIMESTAMP,
        escalated_at TIMESTAMP,
        closed_at TIMESTAMP,
        sla_due_at TIMESTAMP,
        priority TEXT,
        edd_notes JSONB DEFAULT '[]',
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        decided_at TIMESTAMP,
        triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_edd_cases_linked_alert ON edd_cases(linked_monitoring_alert_id);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_linked_review ON edd_cases(linked_periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_origin_context ON edd_cases(origin_context);

    -- Compliance Memo Versions
    CREATE TABLE IF NOT EXISTS compliance_memos (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id),
        version INTEGER DEFAULT 1,
        memo_data TEXT NOT NULL,
        generated_by TEXT REFERENCES users(id),
        ai_recommendation TEXT,
        review_status TEXT DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','approved','rejected')),
        reviewed_by TEXT REFERENCES users(id),
        review_notes TEXT,
        quality_score REAL DEFAULT 0,
        validation_status TEXT DEFAULT 'pending' CHECK(validation_status IN ('pending','pass','pass_with_fixes','fail')),
        validation_issues TEXT DEFAULT '[]',
        validation_run_at TIMESTAMP,
        memo_version TEXT DEFAULT '1.0',
        raw_output_hash TEXT,
        approved_by TEXT REFERENCES users(id),
        approved_at TIMESTAMP,
        -- P12-5 / DCI-006: COMPLIANCE_MEMO_SUPERVISOR_STATUS_VALUES (lockstep test)
        supervisor_status TEXT DEFAULT 'pending' CHECK(supervisor_status IN (
            'pending','CONSISTENT','CONSISTENT_WITH_WARNINGS','INCONSISTENT','approved'
        )),
        supervisor_summary TEXT,
        supervisor_contradictions TEXT DEFAULT '[]',
        rule_violations TEXT DEFAULT '[]',
        -- P12-5 / DCI-006: COMPLIANCE_MEMO_RULE_ENGINE_STATUS_VALUES (lockstep
        -- test).  The memo JSON's same-named KEY uses a different vocabulary
        -- (CLEAN/ENFORCED/VIOLATIONS_DETECTED) — widen this canon FIRST if that
        -- value is ever wired into the column.
        rule_engine_status TEXT DEFAULT 'pending' CHECK(rule_engine_status IN ('pending','pass')),
        blocked BOOLEAN DEFAULT FALSE,
        block_reason TEXT,
        is_stale BOOLEAN DEFAULT FALSE,
        stale_reason TEXT,
        stale_reasons TEXT DEFAULT '[]',
        stale_trigger TEXT,
        stale_marked_at TIMESTAMP,
        pdf_generated_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS edd_findings (
        id SERIAL PRIMARY KEY,
        edd_case_id INTEGER NOT NULL UNIQUE,
        findings_summary TEXT,
        key_concerns TEXT DEFAULT '[]',
        mitigating_evidence TEXT DEFAULT '[]',
        conditions TEXT DEFAULT '[]',
        rationale TEXT,
        supporting_notes TEXT DEFAULT '[]',
        recommended_outcome TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_edd_findings_edd_case_id ON edd_findings(edd_case_id);

    CREATE TABLE IF NOT EXISTS edd_memo_attachments (
        id SERIAL PRIMARY KEY,
        edd_case_id INTEGER NOT NULL,
        application_id TEXT NOT NULL,
        memo_context_kind TEXT NOT NULL,
        memo_id INTEGER,
        periodic_review_id INTEGER,
        attached_by TEXT,
        attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        detached_at TIMESTAMP,
        detached_by TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_edd_case ON edd_memo_attachments(edd_case_id);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_app ON edd_memo_attachments(application_id);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_kind ON edd_memo_attachments(memo_context_kind);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_memo ON edd_memo_attachments(memo_id);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_review ON edd_memo_attachments(periodic_review_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uix_edd_memo_attachments_active_identity
        ON edd_memo_attachments (
            edd_case_id,
            memo_context_kind,
            COALESCE(memo_id, 0),
            COALESCE(periodic_review_id, 0)
        )
        WHERE detached_at IS NULL;

    -- Create indexes for better query performance
    CREATE INDEX IF NOT EXISTS idx_applications_client_id ON applications(client_id);
    CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
    CREATE INDEX IF NOT EXISTS idx_applications_assigned_to ON applications(assigned_to);
    CREATE INDEX IF NOT EXISTS idx_directors_application_id ON directors(application_id);
    CREATE INDEX IF NOT EXISTS idx_ubos_application_id ON ubos(application_id);
    CREATE INDEX IF NOT EXISTS idx_documents_application_id ON documents(application_id);
    CREATE INDEX IF NOT EXISTS idx_compliance_resources_category ON compliance_resources(category);
    CREATE INDEX IF NOT EXISTS idx_compliance_resources_created_at ON compliance_resources(created_at);
    CREATE INDEX IF NOT EXISTS idx_regulatory_documents_status ON regulatory_documents(status);
    CREATE INDEX IF NOT EXISTS idx_regulatory_documents_created_at ON regulatory_documents(created_at);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_application_id ON monitoring_alerts(application_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_linked_edd ON monitoring_alerts(linked_edd_case_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_linked_review ON monitoring_alerts(linked_periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_app ON monitoring_alert_evidence(application_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_application_id ON periodic_reviews(application_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_linked_alert ON periodic_reviews(linked_monitoring_alert_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_linked_edd ON periodic_reviews(linked_edd_case_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_trigger_source ON periodic_reviews(trigger_source);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_status ON periodic_reviews(status);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_outcome ON periodic_reviews(outcome);
    CREATE INDEX IF NOT EXISTS idx_sar_reports_application_id ON sar_reports(application_id);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_application_id ON edd_cases(application_id);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_stage ON edd_cases(stage);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_assigned_officer ON edd_cases(assigned_officer);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_application_id ON compliance_memos(application_id);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_review_status ON compliance_memos(review_status);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_validation_status ON compliance_memos(validation_status);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_blocked ON compliance_memos(blocked);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_created_at ON compliance_memos(created_at);
    CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
    CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
    CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target);
    CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);

    -- Sprint 3: GDPR Data Retention Policy
    CREATE TABLE IF NOT EXISTS data_retention_policies (
        id SERIAL PRIMARY KEY,
        data_category TEXT NOT NULL UNIQUE,
        retention_days INTEGER NOT NULL,
        legal_basis TEXT NOT NULL,
        description TEXT,
        auto_purge BOOLEAN DEFAULT false,
        requires_review BOOLEAN DEFAULT true,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Sprint 3: GDPR Data Subject Access Requests
    CREATE TABLE IF NOT EXISTS data_subject_requests (
        id SERIAL PRIMARY KEY,
        request_type TEXT NOT NULL CHECK(request_type IN ('access','rectification','erasure','portability','restriction','objection')),
        requester_email TEXT NOT NULL,
        requester_name TEXT,
        client_id TEXT REFERENCES clients(id),
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','rejected','expired')),
        description TEXT,
        response_notes TEXT,
        erasure_executed BOOLEAN NOT NULL DEFAULT false,
        retention_outcome TEXT,
        retained_until TEXT,
        retained_categories TEXT,
        erasure_notes TEXT,
        handled_by TEXT REFERENCES users(id),
        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        due_at TIMESTAMP,
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Sprint 3: GDPR Purge Log (immutable audit of what was deleted)
    CREATE TABLE IF NOT EXISTS data_purge_log (
        id SERIAL PRIMARY KEY,
        data_category TEXT NOT NULL,
        record_count INTEGER NOT NULL,
        oldest_record_date TIMESTAMP,
        newest_record_date TIMESTAMP,
        retention_policy_id INTEGER REFERENCES data_retention_policies(id),
        purge_reason TEXT NOT NULL,
        -- P12-8: attribution string, deliberately NOT an FK to users(id) —
        -- the scheduler writes 'system-scheduler' (no users row), and PG
        -- enforces FKs, so the evidence INSERT (and with it the atomic
        -- purge) would fail on every scheduled run. Matches audit_log's
        -- actor columns.
        purged_by TEXT,
        -- P12-8 / DCI-021: regulator-reconstructable purge evidence
        subject_id TEXT,
        application_id TEXT,
        tables_affected TEXT,
        per_table_counts TEXT,
        purge_batch_id TEXT,
        evidence_json TEXT,
        purged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_dsr_status ON data_subject_requests(status);
    CREATE INDEX IF NOT EXISTS idx_dsr_client ON data_subject_requests(client_id);
    CREATE INDEX IF NOT EXISTS idx_purge_log_category ON data_purge_log(data_category);
    -- idx_purge_log_batch on purge_batch_id is created by Migration v2.48
    -- (_ensure_data_purge_log_evidence_columns) AFTER that column is added.
    -- It must NOT be created here: on an EXISTING data_purge_log the CREATE
    -- TABLE above is a no-op, the column doesn't exist yet, and this index
    -- statement crashed schema init on upgrade (P12-8 hotfix).

    -- Rate limiting persistence (survives restarts for auth-critical keys)
    CREATE TABLE IF NOT EXISTS rate_limits (
        id SERIAL PRIMARY KEY,
        key TEXT NOT NULL,
        attempted_at DOUBLE PRECISION NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rate_limits_key ON rate_limits(key);
    CREATE INDEX IF NOT EXISTS idx_rate_limits_attempted ON rate_limits(attempted_at);

    -- Shared fail-closed limiter state for selected sensitive endpoints
    CREATE TABLE IF NOT EXISTS shared_rate_limits (
        key TEXT PRIMARY KEY,
        window_start DOUBLE PRECISION NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        expires_at DOUBLE PRECISION NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_shared_rate_limits_expires_at ON shared_rate_limits(expires_at);

    -- Token revocation persistence (survives restarts)
    CREATE TABLE IF NOT EXISTS revoked_tokens (
        jti TEXT PRIMARY KEY,
        expires_at DOUBLE PRECISION NOT NULL,
        revoked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at);

    -- Supervisor pipeline results (persisted across restarts)
    CREATE TABLE IF NOT EXISTS supervisor_pipeline_results (
        id TEXT PRIMARY KEY,
        pipeline_id TEXT NOT NULL UNIQUE,
        application_id TEXT NOT NULL,
        -- P12-5 / DCI-006: SUPERVISOR_PIPELINE_STATUS_VALUES (lockstep test)
        status TEXT NOT NULL DEFAULT 'running' CHECK(status IN (
            'running','completed','completed_with_errors','awaiting_review','failed'
        )),
        trigger_type TEXT,
        trigger_source TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_pipeline_app ON supervisor_pipeline_results(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_pipeline_status ON supervisor_pipeline_results(status);
    CREATE INDEX IF NOT EXISTS idx_sup_pipeline_completed ON supervisor_pipeline_results(completed_at);

    -- Durable supervisor officer decisions (BSA-003B)
    CREATE TABLE IF NOT EXISTS supervisor_escalations (
        id TEXT PRIMARY KEY,
        pipeline_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        escalation_source TEXT NOT NULL,
        source_id TEXT,
        escalation_level TEXT NOT NULL,
        priority TEXT NOT NULL,
        reason TEXT NOT NULL,
        context_json TEXT DEFAULT '{}',
        assigned_to TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        sla_deadline TIMESTAMP,
        resolved_at TIMESTAMP,
        escalated_by_id TEXT NOT NULL,
        escalated_by_name TEXT NOT NULL,
        escalated_by_role TEXT NOT NULL,
        request_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_app ON supervisor_escalations(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_pipeline ON supervisor_escalations(pipeline_id);
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_status_created ON supervisor_escalations(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_level_status ON supervisor_escalations(escalation_level, status);

    CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
        id TEXT PRIMARY KEY,
        pipeline_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        escalation_id TEXT REFERENCES supervisor_escalations(id),
        review_type TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        reviewer_name TEXT NOT NULL,
        reviewer_role TEXT NOT NULL,
        ai_recommendation TEXT,
        ai_confidence REAL,
        ai_risk_level TEXT,
        rules_recommendation TEXT,
        rules_triggered TEXT DEFAULT '[]',
        contradictions_json TEXT DEFAULT '[]',
        decision TEXT NOT NULL,
        decision_reason TEXT NOT NULL,
        risk_level_assigned TEXT,
        conditions TEXT,
        follow_up_required INTEGER NOT NULL DEFAULT 0,
        follow_up_details TEXT,
        is_ai_override INTEGER NOT NULL DEFAULT 0,
        override_reason TEXT,
        review_started_at TIMESTAMP,
        decision_at TIMESTAMP NOT NULL,
        request_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_app ON supervisor_human_reviews(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_pipeline ON supervisor_human_reviews(pipeline_id);
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_reviewer ON supervisor_human_reviews(reviewer_id);
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_decision_at ON supervisor_human_reviews(decision_at);

    CREATE TABLE IF NOT EXISTS supervisor_overrides (
        id TEXT PRIMARY KEY,
        review_id TEXT NOT NULL REFERENCES supervisor_human_reviews(id),
        application_id TEXT NOT NULL,
        agent_type TEXT,
        override_type TEXT NOT NULL,
        original_value TEXT NOT NULL,
        override_value TEXT NOT NULL,
        reason TEXT NOT NULL,
        officer_id TEXT NOT NULL,
        officer_name TEXT NOT NULL,
        officer_role TEXT NOT NULL,
        approver_id TEXT,
        approver_name TEXT,
        approved_at TIMESTAMP,
        request_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_overrides_app ON supervisor_overrides(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_overrides_review ON supervisor_overrides(review_id);
    CREATE INDEX IF NOT EXISTS idx_sup_overrides_created ON supervisor_overrides(created_at);

    -- Supervisor audit log (production-grade, uses shared DB)
    CREATE TABLE IF NOT EXISTS supervisor_audit_log (
        id TEXT PRIMARY KEY,
        timestamp TIMESTAMP NOT NULL,
        -- P12-5 / DCI-006: SUPERVISOR_AUDIT_EVENT_TYPE_VALUES /
        -- SUPERVISOR_AUDIT_SEVERITY_VALUES (lockstep test).  The legacy-repair
        -- creator (_create_supervisor_audit_log_table) deliberately carries NO
        -- CHECKs so historical rows can be rehashed; v2.47 constrains PG after
        -- verifying the data is clean.
        event_type TEXT NOT NULL CHECK(event_type IN (
            'agent_run_started','agent_run_completed','agent_run_failed',
            'schema_validation_passed','schema_validation_failed',
            'confidence_calculated','confidence_routing',
            'contradiction_detected','contradiction_resolved',
            'rule_triggered','rule_overridden',
            'escalation_created','escalation_assigned','escalation_resolved',
            'human_review_started','human_review_completed','ai_override',
            'pipeline_started','pipeline_completed','pipeline_failed',
            'supervisor_verdict','config_changed',
            'agent_version_changed','prompt_version_changed','system_error'
        )),
        severity TEXT DEFAULT 'info' CHECK(severity IN (
            'critical','high','medium','low','info','warning'
        )),
        pipeline_id TEXT,
        application_id TEXT,
        run_id TEXT,
        agent_type TEXT,
        actor_type TEXT,
        actor_id TEXT,
        actor_name TEXT,
        actor_role TEXT,
        action TEXT NOT NULL,
        detail TEXT,
        data_json TEXT DEFAULT '{}',
        ip_address TEXT,
        session_id TEXT,
        previous_hash TEXT,
        entry_hash TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sup_audit_ts ON supervisor_audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_event ON supervisor_audit_log(event_type);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_app ON supervisor_audit_log(application_id);

    -- Decision records (normalized audit layer)
    CREATE TABLE IF NOT EXISTS decision_records (
        id TEXT PRIMARY KEY,
        application_ref TEXT NOT NULL,
        decision_type TEXT NOT NULL CHECK(decision_type IN (
            'approve','reject','escalate_edd','request_documents','pre_approve','request_info'
        )),
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        confidence_score REAL,
        source TEXT NOT NULL CHECK(source IN ('manual','supervisor','rule_engine')),
        actor_user_id TEXT,
        actor_role TEXT,
        timestamp TIMESTAMP NOT NULL,
        key_flags TEXT DEFAULT '[]',
        override_flag INTEGER DEFAULT 0,
        override_reason TEXT,
        extra_json TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_dec_rec_app ON decision_records(application_ref);
    CREATE INDEX IF NOT EXISTS idx_dec_rec_type ON decision_records(decision_type);
    CREATE INDEX IF NOT EXISTS idx_dec_rec_ts ON decision_records(timestamp);

    -- Screening Reports Normalized (Phase A4: dialect-safe DDL consolidated into init_db)
    -- IF NOT EXISTS guarantees existing production tables are untouched.
    CREATE TABLE IF NOT EXISTS screening_reports_normalized (
        id SERIAL PRIMARY KEY,
        client_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'sumsub',
        normalized_version TEXT NOT NULL DEFAULT '1.0',
        source_screening_report_hash TEXT,
        normalized_report_json TEXT,
        normalization_status TEXT NOT NULL DEFAULT 'success' CHECK(normalization_status IN ('success', 'failed')),
        normalization_error TEXT,
        is_authoritative INTEGER NOT NULL DEFAULT 0 CHECK(is_authoritative = 0),
        source TEXT NOT NULL DEFAULT 'migration_scaffolding',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_screening_normalized_client_app ON screening_reports_normalized(client_id, application_id);
    CREATE INDEX IF NOT EXISTS idx_screening_normalized_app_id ON screening_reports_normalized(application_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_normalized_app_provider_hash ON screening_reports_normalized(application_id, provider, source_screening_report_hash);

    -- D2 provider-pair comparison artifacts (Sumsub-primary / CA-shadow)
    CREATE TABLE IF NOT EXISTS screening_provider_comparisons (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL,
        client_id TEXT NOT NULL,
        primary_provider TEXT NOT NULL,
        shadow_provider TEXT NOT NULL,
        comparison_kind TEXT NOT NULL DEFAULT 'screening_shadow',
        primary_normalized_record_id INTEGER,
        shadow_normalized_record_id INTEGER,
        mismatch_class TEXT NOT NULL,
        comparison_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_provider_comparisons_app ON screening_provider_comparisons(application_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_comparisons_app_pair ON screening_provider_comparisons(application_id, primary_provider, shadow_provider, comparison_kind);

    -- Screening Monitoring Subscriptions (Phase C1.a: ComplyAdvantage scaffolding)
    CREATE TABLE IF NOT EXISTS screening_monitoring_subscriptions (
        id SERIAL PRIMARY KEY,
        client_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        person_key TEXT,
        customer_identifier TEXT NOT NULL,
        external_subscription_id TEXT,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'paused', 'cancelled', 'expired')),
        subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_event_at TIMESTAMP,
        last_webhook_type TEXT,
        monitoring_event_count INTEGER NOT NULL DEFAULT 0,
        is_authoritative INTEGER NOT NULL DEFAULT 0
            CHECK(is_authoritative = 0),
        source TEXT NOT NULL DEFAULT 'migration_scaffolding',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_screening_monitoring_subs_app
        ON screening_monitoring_subscriptions (application_id);
    CREATE INDEX IF NOT EXISTS idx_screening_monitoring_subs_client
        ON screening_monitoring_subscriptions (client_id, application_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_monitoring_subs_customer
        ON screening_monitoring_subscriptions (client_id, provider, customer_identifier);
    """


def _get_sqlite_schema() -> str:
    """SQLite-compatible schema (original format)."""
    return """
    -- Users / Officers
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'analyst' CHECK(role IN ('admin','sco','co','analyst')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Client accounts (applicants)
    CREATE TABLE IF NOT EXISTS clients (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        company_name TEXT,
        -- P12-5 / DCI-006: CLIENT_STATUS_VALUES (kept in lockstep by test)
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
        password_reset_token TEXT,
        password_reset_expires TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Applications
    CREATE TABLE IF NOT EXISTS applications (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        ref TEXT UNIQUE NOT NULL,
        client_id TEXT REFERENCES clients(id),
        company_name TEXT NOT NULL,
        brn TEXT,
        country TEXT,
        sector TEXT,
        entity_type TEXT,
        ownership_structure TEXT,
        prescreening_data TEXT DEFAULT '{}',
        risk_score REAL,
        risk_level TEXT CHECK(risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_dimensions TEXT DEFAULT '{}',
        onboarding_lane TEXT,
        status TEXT DEFAULT 'draft' CHECK(status IN (
            'draft','submitted','prescreening_submitted','pricing_review','pricing_accepted',
            'pre_approval_review','pre_approved',
            'kyc_documents','kyc_submitted','compliance_review','submitted_to_compliance','in_review','under_review',
            'edd_required','approved','rejected','rmi_sent','withdrawn'
        )),
        assigned_to TEXT REFERENCES users(id),
        submitted_at TEXT,
        decided_at TEXT,
        decision_by TEXT REFERENCES users(id),
        decision_notes TEXT,
        pre_approval_decision TEXT,
        pre_approval_notes TEXT,
        pre_approval_officer_id TEXT REFERENCES users(id),
        pre_approval_timestamp TEXT,
        screening_mode TEXT DEFAULT 'live',
        is_fixture INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        inputs_updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Directors
    CREATE TABLE IF NOT EXISTS directors (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT NOT NULL,
        nationality TEXT,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}',
        date_of_birth TEXT,
        country_of_residence TEXT,
        residential_address TEXT,
        date_of_appointment TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- UBOs
    CREATE TABLE IF NOT EXISTS ubos (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT NOT NULL,
        nationality TEXT,
        ownership_pct REAL,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}',
        date_of_birth TEXT,
        country_of_residence TEXT,
        residential_address TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Intermediary shareholders
    CREATE TABLE IF NOT EXISTS intermediaries (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        entity_name TEXT NOT NULL,
        jurisdiction TEXT,
        registration_number TEXT,
        registered_address TEXT,
        ownership_pct REAL,
        owned_or_controlled_by TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Documents
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_id TEXT,
        doc_type TEXT NOT NULL,
        doc_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        s3_key TEXT,
        file_size INTEGER,
        mime_type TEXT,
        file_sha256 TEXT,
        slot_key TEXT,
        is_current INTEGER DEFAULT 1,
        version INTEGER DEFAULT 1,
        superseded_at TEXT,
        superseded_by_document_id TEXT REFERENCES documents(id),
        replaced_reason TEXT,
        replaced_by_user_id TEXT,
        expiry_date TEXT,
        valid_until TEXT,
        expiry_source TEXT,
        expiry_confidence REAL,
        expiry_extracted_at TEXT,
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','in_progress','verified','flagged','failed','skipped')),
        verification_results TEXT DEFAULT '{}',
        review_status TEXT DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','info_requested')),
        review_comment TEXT,
        reviewed_by TEXT REFERENCES users(id),
        evidence_class TEXT,
        evidence_classification_note TEXT,
        evidence_classified_by TEXT REFERENCES users(id),
        evidence_classified_at TEXT,
        workflow_test_accepted INTEGER DEFAULT 0,
        workflow_test_acceptance_reason TEXT,
        workflow_test_accepted_by TEXT REFERENCES users(id),
        workflow_test_accepted_at TEXT,
        workflow_test_acceptance_environment TEXT,
        uploaded_by TEXT REFERENCES users(id),
        uploaded_by_actor_type TEXT,
        uploaded_by_actor_id TEXT,
        uploaded_by_display TEXT,
        upload_source TEXT,
        uploaded_at TEXT DEFAULT (datetime('now')),
        verified_at TEXT,
        reviewed_at TEXT
    );

    -- Async verification jobs (dark behind FF_ASYNC_VERIFY)
    CREATE TABLE IF NOT EXISTS verification_jobs (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
        priority INTEGER NOT NULL DEFAULT 100,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after TEXT DEFAULT (datetime('now')),
        locked_by TEXT,
        locked_at TEXT,
        last_error TEXT,
        job_metadata TEXT DEFAULT '{}',
        created_by TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_verification_jobs_status_run_after
        ON verification_jobs(status, run_after, priority, created_at);
    CREATE INDEX IF NOT EXISTS idx_verification_jobs_document
        ON verification_jobs(document_id, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_verification_jobs_active_document
        ON verification_jobs(document_id)
        WHERE status IN ('pending','retrying','in_progress');

    -- Async screening jobs for submit-time provider work.
    CREATE TABLE IF NOT EXISTS screening_jobs (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        submit_attempt_id TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'complyadvantage',
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
        priority INTEGER NOT NULL DEFAULT 100,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after TEXT DEFAULT (datetime('now')),
        locked_by TEXT,
        locked_at TEXT,
        last_error TEXT,
        job_metadata TEXT DEFAULT '{}',
        created_by TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_screening_jobs_status_run_after
        ON screening_jobs(status, run_after, priority, created_at);
    CREATE INDEX IF NOT EXISTS idx_screening_jobs_application
        ON screening_jobs(application_id, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_jobs_active_application
        ON screening_jobs(application_id)
        WHERE status IN ('pending','retrying','in_progress');

    -- Compliance Resources
    CREATE TABLE IF NOT EXISTS compliance_resources (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        slug TEXT UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        category TEXT DEFAULT 'internal',
        resource_type TEXT DEFAULT 'uploaded' CHECK(resource_type IN ('system','uploaded')),
        file_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        s3_key TEXT,
        mime_type TEXT,
        file_size INTEGER,
        uploaded_by TEXT REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Regulatory Intelligence Documents
    CREATE TABLE IF NOT EXISTS regulatory_documents (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        title TEXT NOT NULL,
        regulator TEXT NOT NULL,
        jurisdiction TEXT NOT NULL,
        doc_type TEXT NOT NULL,
        publication_date TEXT,
        effective_date TEXT,
        file_name TEXT,
        file_path TEXT,
        s3_key TEXT,
        mime_type TEXT,
        file_size INTEGER,
        source_text TEXT,
        status TEXT DEFAULT 'uploaded' CHECK(status IN ('uploaded','analysed','review_required','analysis_failed')),
        analysis_source TEXT,
        analysis_summary TEXT DEFAULT '{}',
        audit_trail TEXT DEFAULT '[]',
        uploaded_by TEXT REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Risk Model Configuration
    CREATE TABLE IF NOT EXISTS risk_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        dimensions TEXT NOT NULL DEFAULT '{}',
        thresholds TEXT NOT NULL DEFAULT '{}',
        country_risk_scores TEXT DEFAULT '{}',
        sector_risk_scores TEXT DEFAULT '{}',
        entity_type_scores TEXT DEFAULT '{}',
        updated_at TEXT DEFAULT (datetime('now')),
        updated_by TEXT REFERENCES users(id)
    );

    -- PR-CR1: Canonical country-risk source governance snapshots
    CREATE TABLE IF NOT EXISTS country_risk_snapshots (
        id TEXT PRIMARY KEY,
        version TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        source_name TEXT NOT NULL,
        source_url TEXT,
        source_publication_date TEXT,
        effective_date TEXT NOT NULL,
        imported_at TEXT DEFAULT (datetime('now')),
        imported_by TEXT NOT NULL DEFAULT 'system',
        last_checked_at TEXT DEFAULT (datetime('now')),
        checksum TEXT NOT NULL,
        freshness_days INTEGER NOT NULL DEFAULT 180,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS country_risk_entries (
        id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL REFERENCES country_risk_snapshots(id),
        country_name TEXT NOT NULL,
        country_key TEXT NOT NULL,
        iso_alpha2 TEXT,
        iso_alpha3 TEXT,
        risk_rating TEXT NOT NULL,
        risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 1 AND 4),
        fatf_status TEXT NOT NULL DEFAULT 'none',
        sanctions_status TEXT NOT NULL DEFAULT 'none',
        high_risk_status TEXT NOT NULL DEFAULT 'none',
        source_name TEXT NOT NULL,
        source_url TEXT,
        source_publication_date TEXT,
        effective_date TEXT NOT NULL,
        imported_at TEXT DEFAULT (datetime('now')),
        imported_by TEXT NOT NULL DEFAULT 'system',
        status TEXT NOT NULL DEFAULT 'active',
        checksum TEXT NOT NULL,
        notes TEXT,
        previous_risk_rating TEXT,
        previous_fatf_status TEXT,
        UNIQUE(snapshot_id, country_key)
    );

    CREATE INDEX IF NOT EXISTS idx_country_risk_entries_lookup
        ON country_risk_entries(snapshot_id, country_key, status);

    CREATE INDEX IF NOT EXISTS idx_country_risk_entries_fatf
        ON country_risk_entries(fatf_status, status);

    -- System Settings
    CREATE TABLE IF NOT EXISTS system_settings (
        id INTEGER PRIMARY KEY DEFAULT 1,
        company_name TEXT NOT NULL DEFAULT 'Onboarda Ltd',
        licence_number TEXT DEFAULT 'FSC-PIS-2024-001',
        default_retention_years INTEGER NOT NULL DEFAULT 7,
        auto_approve_max_score INTEGER NOT NULL DEFAULT 40,
        edd_threshold_score INTEGER NOT NULL DEFAULT 55,
        updated_at TEXT DEFAULT (datetime('now')),
        updated_by TEXT REFERENCES users(id)
    );

    -- Enhanced / EDD requirement settings
    CREATE TABLE IF NOT EXISTS enhanced_requirement_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_key TEXT NOT NULL,
        trigger_label TEXT NOT NULL,
        trigger_category TEXT NOT NULL DEFAULT 'risk',
        requirement_key TEXT NOT NULL,
        requirement_label TEXT NOT NULL,
        requirement_description TEXT,
        audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
        requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
        subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
        blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
        waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
        waiver_roles TEXT DEFAULT '[]',
        mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        sort_order INTEGER NOT NULL DEFAULT 100,
        applies_when TEXT DEFAULT '{}',
        client_safe_label TEXT,
        client_safe_description TEXT,
        internal_notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        created_by TEXT REFERENCES users(id),
        updated_at TEXT DEFAULT (datetime('now')),
        updated_by TEXT REFERENCES users(id),
        UNIQUE(trigger_key, requirement_key)
    );
    CREATE INDEX IF NOT EXISTS idx_enhanced_req_trigger ON enhanced_requirement_rules(trigger_key);
    CREATE INDEX IF NOT EXISTS idx_enhanced_req_active ON enhanced_requirement_rules(active);
    CREATE INDEX IF NOT EXISTS idx_enhanced_req_audience ON enhanced_requirement_rules(audience);

    -- Application-specific generated Enhanced / EDD requirements
    CREATE TABLE IF NOT EXISTS application_enhanced_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        source_rule_id INTEGER REFERENCES enhanced_requirement_rules(id),
        trigger_key TEXT NOT NULL,
        trigger_label TEXT NOT NULL,
        trigger_category TEXT NOT NULL DEFAULT 'risk',
        requirement_key TEXT NOT NULL,
        requirement_label TEXT NOT NULL,
        requirement_description TEXT,
        audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
        requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
        subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
        blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
        waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
        waiver_roles TEXT DEFAULT '[]',
        mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
        status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN ('generated','requested','uploaded','under_review','accepted','rejected','waived','cancelled')),
        generation_source TEXT NOT NULL DEFAULT 'manual_api',
        trigger_reason TEXT,
        trigger_context TEXT DEFAULT '{}',
        linked_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
        monitoring_alert_id INTEGER,
        monitoring_document_id TEXT,
        due_date TEXT,
        linked_rmi_item_id TEXT,
        requested_at TEXT,
        requested_by TEXT REFERENCES users(id),
        uploaded_at TEXT,
        client_response_text TEXT,
        client_response_at TEXT,
        client_response_by TEXT REFERENCES clients(id),
        reviewed_at TEXT,
        reviewed_by TEXT REFERENCES users(id),
        review_notes TEXT,
        waived_at TEXT,
        waived_by TEXT REFERENCES users(id),
        waiver_reason TEXT,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at TEXT DEFAULT (datetime('now')),
        created_by TEXT REFERENCES users(id),
        updated_at TEXT DEFAULT (datetime('now')),
        updated_by TEXT REFERENCES users(id),
        UNIQUE(application_id, trigger_key, requirement_key)
    );
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_app ON application_enhanced_requirements(application_id);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_rule ON application_enhanced_requirements(source_rule_id);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_trigger ON application_enhanced_requirements(trigger_key);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_status ON application_enhanced_requirements(status);
    CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_active ON application_enhanced_requirements(active);

    -- AI Agents Configuration
    CREATE TABLE IF NOT EXISTS ai_agents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_number INTEGER UNIQUE NOT NULL,
        name TEXT NOT NULL,
        icon TEXT DEFAULT '🤖',
        stage TEXT NOT NULL,
        description TEXT,
        enabled INTEGER DEFAULT 1,
        checks TEXT DEFAULT '[]',
        supervisor_agent_type TEXT,
        risk_dimensions TEXT DEFAULT '[]',
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- AI Verification Checks Configuration
    CREATE TABLE IF NOT EXISTS ai_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL CHECK(category IN ('entity','person')),
        doc_type TEXT NOT NULL,
        doc_name TEXT NOT NULL,
        checks TEXT DEFAULT '[]',
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Agent Execution Traceability Log
    CREATE TABLE IF NOT EXISTS agent_executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL,
        document_id TEXT,
        agent_name TEXT NOT NULL,
        agent_number INTEGER,
        -- P12-5 / DCI-006: AGENT_EXECUTION_STATUS_VALUES (lockstep test)
        status TEXT NOT NULL CHECK(status IN (
            'verified','flagged','skipped','completed',
            'pending','in_progress','failed','error'
        )),
        checks_json TEXT,
        flags_json TEXT,
        requires_review INTEGER DEFAULT 0,
        -- P12-5 / DCI-006: AGENT_EXECUTION_SOURCE_VALUES (lockstep test)
        source TEXT DEFAULT 'ai' CHECK(source IN ('ai','stored_screening_results')),
        started_at TEXT,
        completed_at TEXT DEFAULT (datetime('now')),
        error_message TEXT
    );

    -- Screening Review Dispositions
    CREATE TABLE IF NOT EXISTS screening_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        subject_type TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN ('cleared','escalated','follow_up_required')),
        notes TEXT,
        disposition_code TEXT,
        rationale TEXT,
        sensitivity_flags TEXT DEFAULT '[]',
        requires_four_eyes INTEGER DEFAULT 0,
        reviewer_id TEXT REFERENCES users(id),
        reviewer_name TEXT,
        second_reviewer_id TEXT REFERENCES users(id),
        second_reviewer_name TEXT,
        second_disposition_code TEXT,
        second_rationale TEXT,
        second_reviewed_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(application_id, subject_type, subject_name)
    );

    -- SRP per-hit disposition (see the PostgreSQL schema for the full note).
    -- Granular per-hit record backing the review UI + audit trail; the
    -- subject-level rollup still feeds the frozen gates via screening_reviews.
    CREATE TABLE IF NOT EXISTS screening_hit_dispositions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        subject_type TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        hit_id TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN ('match','cleared','escalated','follow_up_required')),
        materiality TEXT CHECK(materiality IN ('high','moderate','nonmaterial','insufficient')),
        rationale TEXT,
        reviewer_id TEXT REFERENCES users(id),
        reviewer_name TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(application_id, subject_type, subject_name, hit_id)
    );

    -- SRP-2: superseded screening-report snapshots. A governed re-screen
    -- archives the outgoing report here before replacement — screening
    -- evidence is regulated and is never destroyed by a refresh.
    CREATE TABLE IF NOT EXISTS screening_report_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL,
        application_ref TEXT,
        archived_at TEXT DEFAULT (datetime('now')),
        archived_by TEXT NOT NULL,
        reason TEXT NOT NULL,
        report_hash TEXT NOT NULL,
        report_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_screening_report_archive_app
        ON screening_report_archive(application_id);

    -- Audit Trail
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT (datetime('now')),
        user_id TEXT,
        user_name TEXT,
        user_role TEXT,
        action TEXT NOT NULL,
        target TEXT,
        application_id TEXT,
        detail TEXT,
        ip_address TEXT,
        -- P12-9 / DCI-028: request correlation id (nullable; chain-safe)
        request_id TEXT
    );

    -- Notifications
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT REFERENCES users(id),
        title TEXT NOT NULL,
        message TEXT,
        read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Session / Save & Resume tokens
    CREATE TABLE IF NOT EXISTS client_sessions (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        client_id TEXT REFERENCES clients(id),
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        form_data TEXT DEFAULT '{}',
        last_step INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Monitoring Alerts
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        provider TEXT,
        case_identifier TEXT,
        discovered_via TEXT NOT NULL DEFAULT 'webhook_live'
            CHECK(discovered_via IN ('webhook_live','webhook_backfill','manual_backfill','manual','officer_created','document_health')),
        discovered_at TEXT DEFAULT (datetime('now')),
        backfill_run_id TEXT,
        client_name TEXT,
        alert_type TEXT,
        severity TEXT,
        detected_by TEXT,
        summary TEXT,
        source_reference TEXT,
        ai_recommendation TEXT,
        status TEXT DEFAULT 'open',
        officer_action TEXT,
        officer_notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        reviewed_at TEXT,
        reviewed_by TEXT REFERENCES users(id),
        linked_periodic_review_id INTEGER,
        linked_edd_case_id INTEGER,
        triaged_at TEXT,
        assigned_at TEXT,
        resolved_at TEXT
    );

    -- Periodic Reviews
    CREATE TABLE IF NOT EXISTS periodic_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        client_name TEXT,
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        last_review_date TEXT,
        next_review_date TEXT,
        trigger_type TEXT,
        trigger_reason TEXT,
        trigger_source TEXT,
        linked_monitoring_alert_id INTEGER,
        linked_edd_case_id INTEGER,
        review_reason TEXT,
        previous_risk_level TEXT CHECK(previous_risk_level IS NULL OR previous_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        new_risk_level TEXT CHECK(new_risk_level IS NULL OR new_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        review_memo TEXT,
        status TEXT DEFAULT 'pending',
        due_date TEXT,
        started_at TEXT,
        completed_at TEXT,
        assigned_officer TEXT REFERENCES users(id),
        assigned_by TEXT REFERENCES users(id),
        assigned_at TEXT,
        reassigned_reason TEXT,
        closed_at TEXT,
        sla_due_at TEXT,
        priority TEXT,
        decision TEXT,
        decision_reason TEXT,
        outcome TEXT,
        outcome_reason TEXT,
        outcome_recorded_at TEXT,
        review_cycle_number INTEGER DEFAULT 1,
        review_type TEXT,
        policy_version TEXT,
        frequency_months INTEGER,
        calculation_basis TEXT,
        legacy_import INTEGER DEFAULT 0,
        legacy_source_type TEXT,
        legacy_source_note TEXT,
        legacy_review_evidence_note TEXT,
        legacy_confidence TEXT,
        legacy_entered_by TEXT REFERENCES users(id),
        legacy_entered_at TEXT,
        legacy_sco_acknowledged_by TEXT REFERENCES users(id),
        legacy_sco_acknowledged_at TEXT,
        import_requires_ack INTEGER DEFAULT 0,
        baseline_status TEXT,
        baseline_date TEXT,
        baseline_cadence_months INTEGER,
        baseline_note TEXT,
        material_change_attestation TEXT,
        material_change_categories TEXT DEFAULT '[]',
        risk_change_attestation TEXT,
        risk_rerate_reason TEXT,
        risk_rerated_by TEXT REFERENCES users(id),
        risk_rerated_at TEXT,
        client_attestation_status TEXT DEFAULT 'not_started',
        client_attestation_payload TEXT DEFAULT '{}',
        client_attestation_saved_at TEXT,
        client_attestation_submitted_at TEXT,
        client_attestation_submitted_by TEXT REFERENCES clients(id),
        client_attestation_questionnaire_version TEXT,
        officer_rationale TEXT,
        officer_findings_note TEXT,
        officer_deficiencies_note TEXT,
        officer_internal_review_note TEXT,
        findings_updated_by TEXT REFERENCES users(id),
        findings_updated_at TEXT,
        memo_status TEXT,
        periodic_review_memo_id INTEGER,
        risk_reassessment_status TEXT DEFAULT 'not_started',
        risk_impact_category TEXT,
        officer_risk_decision TEXT,
        confirmed_risk_level TEXT CHECK(confirmed_risk_level IS NULL OR confirmed_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_reassessment_rationale TEXT,
        risk_reassessment_saved_by TEXT REFERENCES users(id),
        risk_reassessment_saved_at TEXT,
        senior_review_required INTEGER DEFAULT 0,
        senior_review_reason TEXT,
        memo_addendum_status TEXT DEFAULT 'not_generated',
        memo_addendum_generated_at TEXT,
        memo_addendum_finalized_at TEXT,
        memo_addendum_finalized_by TEXT REFERENCES users(id),
        client_notification_status TEXT DEFAULT 'not_sent',
        initial_notification_sent_at TEXT,
        last_reminder_sent_at TEXT,
        reminder_count INTEGER DEFAULT 0,
        last_notification_error TEXT,
        officer_alert_status TEXT,
        officer_alerted_at TEXT,
        notification_channel TEXT DEFAULT 'portal',
        next_reminder_due_at TEXT,
        required_items TEXT,
        required_items_generated_at TEXT,
        state_changed_at TEXT,
        decided_by TEXT REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS periodic_review_memos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodic_review_id INTEGER NOT NULL,
        application_id TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        memo_data TEXT NOT NULL,
        memo_context TEXT NOT NULL,
        generated_at TEXT DEFAULT (datetime('now')),
        generated_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'generated',
        UNIQUE(periodic_review_id, version)
    );
    CREATE INDEX IF NOT EXISTS idx_prm_review ON periodic_review_memos(periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_prm_app ON periodic_review_memos(application_id);

    CREATE TABLE IF NOT EXISTS periodic_review_evidence_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodic_review_id INTEGER NOT NULL REFERENCES periodic_reviews(id) ON DELETE CASCADE,
        requirement_id TEXT,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        link_type TEXT,
        linked_by TEXT REFERENCES users(id),
        linked_at TEXT DEFAULT (datetime('now')),
        note TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_prev_links_review ON periodic_review_evidence_links(periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_prev_links_document ON periodic_review_evidence_links(document_id);

    -- Monitoring Agent Status
    CREATE TABLE IF NOT EXISTS monitoring_agent_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_name TEXT,
        agent_type TEXT,
        last_run TEXT,
        next_run TEXT,
        run_frequency TEXT,
        clients_monitored INTEGER,
        alerts_generated INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active'
    );

    -- M2.2 four-eyes: maker-checker review requests for material alert clears.
    CREATE TABLE IF NOT EXISTS monitoring_alert_review_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
        tier INTEGER,
        requested_outcome TEXT,
        dismissal_reason TEXT,
        rationale TEXT,
        evidence_ref TEXT,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','senior_cleared')),
        initiated_by TEXT,
        initiated_at TEXT DEFAULT (datetime('now')),
        approved_by TEXT,
        approved_at TEXT,
        approval_note TEXT,
        rejection_reason TEXT,
        second_review_bypassed INTEGER DEFAULT 0,
        sampled_for_qa INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_monitoring_review_requests_alert
        ON monitoring_alert_review_requests(alert_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_review_requests_state
        ON monitoring_alert_review_requests(state);

    -- M2.1 PR-2: officer follow-up tracker for monitoring alerts. Additive
    -- annotation ledger; NEVER changes monitoring_alerts.status (aging/next-step
    -- are derived from these rows, not stored on the alert).
    CREATE TABLE IF NOT EXISTS monitoring_alert_followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
        action TEXT NOT NULL DEFAULT 'note'
            CHECK(action IN ('note','next_step','snooze_until','contacted_client','pending_review','other')),
        note TEXT,
        due_at TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        resolved_at TEXT,
        resolved_by TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_monitoring_followups_alert
        ON monitoring_alert_followups(alert_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_followups_open
        ON monitoring_alert_followups(alert_id, resolved_at);

    -- M2.1 PR-4: officer-triggered overdue escalation ledger. Additive
    -- metadata only; the canonical alert status still moves through the
    -- existing monitoring decision transition to monitoring_alerts.status =
    -- 'escalated'.
    CREATE TABLE IF NOT EXISTS monitoring_alert_escalations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
        reason TEXT NOT NULL,
        escalated_by TEXT,
        escalated_by_role TEXT,
        escalated_at TEXT DEFAULT (datetime('now')),
        prior_status TEXT,
        new_status TEXT,
        sla_state TEXT,
        days_overdue INTEGER,
        sla_due_at TEXT,
        sla_days INTEGER,
        alert_severity_at_escalation TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_monitoring_alert_escalations_alert
        ON monitoring_alert_escalations(alert_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alert_escalations_actor
        ON monitoring_alert_escalations(escalated_by);

    -- Client Notifications
    CREATE TABLE IF NOT EXISTS client_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        client_id TEXT REFERENCES clients(id),
        notification_type TEXT,
        title TEXT NOT NULL,
        message TEXT,
        documents_list TEXT,
        rmi_request_id TEXT,
        read_status INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        read_at TEXT
    );

    -- Structured Request for More Information (RMI)
    CREATE TABLE IF NOT EXISTS rmi_requests (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        client_id TEXT REFERENCES clients(id),
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending_review','partially_fulfilled','fulfilled','cancelled')),
        reason TEXT NOT NULL,
        deadline TEXT NOT NULL,
        created_by TEXT REFERENCES users(id),
        created_by_name TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        fulfilled_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_rmi_requests_app ON rmi_requests(application_id);
    CREATE INDEX IF NOT EXISTS idx_rmi_requests_client ON rmi_requests(client_id);
    CREATE INDEX IF NOT EXISTS idx_rmi_requests_status ON rmi_requests(status);

    CREATE TABLE IF NOT EXISTS rmi_request_items (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL REFERENCES rmi_requests(id) ON DELETE CASCADE,
        doc_type TEXT NOT NULL,
        label TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','uploaded','accepted','rejected')),
        document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
        uploaded_at TEXT,
        reviewed_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_rmi_items_request ON rmi_request_items(request_id);
    CREATE INDEX IF NOT EXISTS idx_rmi_items_doc ON rmi_request_items(document_id);

    CREATE TABLE IF NOT EXISTS idv_resolutions (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        application_ref TEXT,
        person_id TEXT,
        person_type TEXT,
        person_name TEXT,
        prior_provider_status TEXT,
        prior_review_answer TEXT,
        resolution_status TEXT NOT NULL,
        resolution_outcome TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        evidence_reviewed TEXT NOT NULL DEFAULT '[]',
        rationale TEXT NOT NULL,
        confirmation_text TEXT,
        senior_approver_id TEXT,
        resolved_by TEXT NOT NULL,
        resolved_by_name TEXT,
        resolved_by_role TEXT NOT NULL,
        ip_address TEXT,
        user_agent TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_idv_resolutions_app ON idv_resolutions(application_id);
    CREATE INDEX IF NOT EXISTS idx_idv_resolutions_subject ON idv_resolutions(application_id, person_type, person_id, person_name);
    CREATE INDEX IF NOT EXISTS idx_idv_resolutions_status ON idv_resolutions(resolution_status);

    -- Suspicious Activity Reports (SAR)
    CREATE TABLE IF NOT EXISTS sar_reports (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
        alert_id INTEGER REFERENCES monitoring_alerts(id),
        sar_reference TEXT UNIQUE,
        report_type TEXT DEFAULT 'SAR' CHECK(report_type IN ('SAR','STR','CTR','MLRO')),
        subject_name TEXT NOT NULL,
        subject_type TEXT DEFAULT 'individual' CHECK(subject_type IN ('individual','entity')),
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        narrative TEXT NOT NULL,
        indicators TEXT DEFAULT '[]',
        transaction_details TEXT DEFAULT '{}',
        supporting_documents TEXT DEFAULT '[]',
        filing_status TEXT DEFAULT 'draft' CHECK(filing_status IN ('draft','pending_review','approved','filed','rejected','archived')),
        prepared_by TEXT REFERENCES users(id),
        reviewed_by TEXT REFERENCES users(id),
        approved_by TEXT REFERENCES users(id),
        filed_at TEXT,
        regulatory_body TEXT DEFAULT 'FIU Mauritius',
        external_reference TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Transaction Ledger (Agent 8: Behaviour & Risk Drift Detection)
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        transaction_ref TEXT,
        transaction_date TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT DEFAULT 'USD',
        direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound','internal')),
        counterparty_name TEXT,
        counterparty_country TEXT,
        product_type TEXT,
        channel TEXT,
        description TEXT,
        risk_flags TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_transactions_application_id ON transactions(application_id);
    CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
    CREATE INDEX IF NOT EXISTS idx_transactions_counterparty_country ON transactions(counterparty_country);

    -- Enhanced Due Diligence (EDD) Cases
    CREATE TABLE IF NOT EXISTS edd_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id),
        client_name TEXT NOT NULL,
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_score REAL,
        stage TEXT DEFAULT 'triggered' CHECK(stage IN ('triggered','information_gathering','analysis','pending_senior_review','edd_approved','edd_rejected')),
        assigned_officer TEXT REFERENCES users(id),
        senior_reviewer TEXT REFERENCES users(id),
        trigger_source TEXT DEFAULT 'officer_decision',
        trigger_notes TEXT,
        origin_context TEXT,
        linked_monitoring_alert_id INTEGER,
        linked_periodic_review_id INTEGER,
        assigned_at TEXT,
        escalated_at TEXT,
        closed_at TEXT,
        sla_due_at TEXT,
        priority TEXT,
        edd_notes TEXT DEFAULT '[]',
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        decided_at TEXT,
        triggered_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_edd_cases_linked_alert ON edd_cases(linked_monitoring_alert_id);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_linked_review ON edd_cases(linked_periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_edd_cases_origin_context ON edd_cases(origin_context);

    -- Compliance Memo Versions
    CREATE TABLE IF NOT EXISTS compliance_memos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id),
        version INTEGER DEFAULT 1,
        memo_data TEXT NOT NULL,
        generated_by TEXT REFERENCES users(id),
        ai_recommendation TEXT,
        review_status TEXT DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','approved','rejected')),
        reviewed_by TEXT REFERENCES users(id),
        review_notes TEXT,
        quality_score REAL DEFAULT 0,
        validation_status TEXT DEFAULT 'pending' CHECK(validation_status IN ('pending','pass','pass_with_fixes','fail')),
        validation_issues TEXT DEFAULT '[]',
        validation_run_at TEXT,
        memo_version TEXT DEFAULT '1.0',
        raw_output_hash TEXT,
        approved_by TEXT REFERENCES users(id),
        approved_at TEXT,
        -- P12-5 / DCI-006: COMPLIANCE_MEMO_SUPERVISOR_STATUS_VALUES (lockstep test)
        supervisor_status TEXT DEFAULT 'pending' CHECK(supervisor_status IN (
            'pending','CONSISTENT','CONSISTENT_WITH_WARNINGS','INCONSISTENT','approved'
        )),
        supervisor_summary TEXT,
        supervisor_contradictions TEXT DEFAULT '[]',
        rule_violations TEXT DEFAULT '[]',
        -- P12-5 / DCI-006: COMPLIANCE_MEMO_RULE_ENGINE_STATUS_VALUES (lockstep
        -- test).  The memo JSON's same-named KEY uses a different vocabulary
        -- (CLEAN/ENFORCED/VIOLATIONS_DETECTED) — widen this canon FIRST if that
        -- value is ever wired into the column.
        rule_engine_status TEXT DEFAULT 'pending' CHECK(rule_engine_status IN ('pending','pass')),
        blocked INTEGER DEFAULT 0,
        block_reason TEXT,
        is_stale INTEGER DEFAULT 0,
        stale_reason TEXT,
        stale_reasons TEXT DEFAULT '[]',
        stale_trigger TEXT,
        stale_marked_at TEXT,
        pdf_generated_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS edd_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        edd_case_id INTEGER NOT NULL UNIQUE,
        findings_summary TEXT,
        key_concerns TEXT DEFAULT '[]',
        mitigating_evidence TEXT DEFAULT '[]',
        conditions TEXT DEFAULT '[]',
        rationale TEXT,
        supporting_notes TEXT DEFAULT '[]',
        recommended_outcome TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_by TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_edd_findings_edd_case_id ON edd_findings(edd_case_id);

    CREATE TABLE IF NOT EXISTS edd_memo_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        edd_case_id INTEGER NOT NULL,
        application_id TEXT NOT NULL,
        memo_context_kind TEXT NOT NULL,
        memo_id INTEGER,
        periodic_review_id INTEGER,
        attached_by TEXT,
        attached_at TEXT DEFAULT (datetime('now')),
        detached_at TEXT,
        detached_by TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_edd_case ON edd_memo_attachments(edd_case_id);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_app ON edd_memo_attachments(application_id);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_kind ON edd_memo_attachments(memo_context_kind);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_memo ON edd_memo_attachments(memo_id);
    CREATE INDEX IF NOT EXISTS idx_edd_memo_attachments_review ON edd_memo_attachments(periodic_review_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uix_edd_memo_attachments_active_identity
        ON edd_memo_attachments (
            edd_case_id,
            memo_context_kind,
            COALESCE(memo_id, 0),
            COALESCE(periodic_review_id, 0)
        )
        WHERE detached_at IS NULL;

    CREATE INDEX IF NOT EXISTS idx_compliance_memos_application_id ON compliance_memos(application_id);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_review_status ON compliance_memos(review_status);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_validation_status ON compliance_memos(validation_status);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_blocked ON compliance_memos(blocked);
    CREATE INDEX IF NOT EXISTS idx_compliance_memos_created_at ON compliance_memos(created_at);
    CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
    CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
    CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target);
    CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_applications_client_id ON applications(client_id);
    CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
    CREATE INDEX IF NOT EXISTS idx_applications_assigned_to ON applications(assigned_to);
    CREATE INDEX IF NOT EXISTS idx_directors_application_id ON directors(application_id);
    CREATE INDEX IF NOT EXISTS idx_ubos_application_id ON ubos(application_id);
    CREATE INDEX IF NOT EXISTS idx_documents_application_id ON documents(application_id);
    CREATE INDEX IF NOT EXISTS idx_compliance_resources_category ON compliance_resources(category);
    CREATE INDEX IF NOT EXISTS idx_compliance_resources_created_at ON compliance_resources(created_at);
    CREATE INDEX IF NOT EXISTS idx_regulatory_documents_status ON regulatory_documents(status);
    CREATE INDEX IF NOT EXISTS idx_regulatory_documents_created_at ON regulatory_documents(created_at);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_linked_edd ON monitoring_alerts(linked_edd_case_id);
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_linked_review ON monitoring_alerts(linked_periodic_review_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_linked_alert ON periodic_reviews(linked_monitoring_alert_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_linked_edd ON periodic_reviews(linked_edd_case_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_trigger_source ON periodic_reviews(trigger_source);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_status ON periodic_reviews(status);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_outcome ON periodic_reviews(outcome);

    -- Sprint 3: GDPR Data Retention Policy
    CREATE TABLE IF NOT EXISTS data_retention_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_category TEXT NOT NULL UNIQUE,
        retention_days INTEGER NOT NULL,
        legal_basis TEXT NOT NULL,
        description TEXT,
        auto_purge INTEGER DEFAULT 0,
        requires_review INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Sprint 3: GDPR Data Subject Access Requests
    CREATE TABLE IF NOT EXISTS data_subject_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_type TEXT NOT NULL CHECK(request_type IN ('access','rectification','erasure','portability','restriction','objection')),
        requester_email TEXT NOT NULL,
        requester_name TEXT,
        client_id TEXT REFERENCES clients(id),
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','rejected','expired')),
        description TEXT,
        response_notes TEXT,
        erasure_executed INTEGER NOT NULL DEFAULT 0,
        retention_outcome TEXT,
        retained_until TEXT,
        retained_categories TEXT,
        erasure_notes TEXT,
        handled_by TEXT REFERENCES users(id),
        received_at TEXT DEFAULT (datetime('now')),
        due_at TEXT,
        completed_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Sprint 3: GDPR Purge Log (immutable audit of what was deleted)
    CREATE TABLE IF NOT EXISTS data_purge_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_category TEXT NOT NULL,
        record_count INTEGER NOT NULL,
        oldest_record_date TEXT,
        newest_record_date TEXT,
        retention_policy_id INTEGER REFERENCES data_retention_policies(id),
        purge_reason TEXT NOT NULL,
        -- P12-8: attribution string, deliberately NOT an FK to users(id) —
        -- the scheduler writes 'system-scheduler' (no users row), and PG
        -- enforces FKs, so the evidence INSERT (and with it the atomic
        -- purge) would fail on every scheduled run. Matches audit_log's
        -- actor columns.
        purged_by TEXT,
        -- P12-8 / DCI-021: regulator-reconstructable purge evidence
        subject_id TEXT,
        application_id TEXT,
        tables_affected TEXT,
        per_table_counts TEXT,
        purge_batch_id TEXT,
        evidence_json TEXT,
        purged_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_dsr_status ON data_subject_requests(status);
    CREATE INDEX IF NOT EXISTS idx_dsr_client ON data_subject_requests(client_id);
    CREATE INDEX IF NOT EXISTS idx_purge_log_category ON data_purge_log(data_category);
    -- idx_purge_log_batch on purge_batch_id is created by Migration v2.48
    -- (_ensure_data_purge_log_evidence_columns) AFTER that column is added.
    -- It must NOT be created here: on an EXISTING data_purge_log the CREATE
    -- TABLE above is a no-op, the column doesn't exist yet, and this index
    -- statement crashed schema init on upgrade (P12-8 hotfix).

    -- Rate limiting persistence (survives restarts for auth-critical keys)
    CREATE TABLE IF NOT EXISTS rate_limits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        attempted_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rate_limits_key ON rate_limits(key);
    CREATE INDEX IF NOT EXISTS idx_rate_limits_attempted ON rate_limits(attempted_at);

    -- Shared fail-closed limiter state for selected sensitive endpoints
    CREATE TABLE IF NOT EXISTS shared_rate_limits (
        key TEXT PRIMARY KEY,
        window_start REAL NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        expires_at REAL NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_shared_rate_limits_expires_at ON shared_rate_limits(expires_at);

    -- Token revocation persistence (survives restarts)
    CREATE TABLE IF NOT EXISTS revoked_tokens (
        jti TEXT PRIMARY KEY,
        expires_at REAL NOT NULL,
        revoked_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at);

    -- Supervisor pipeline results (persisted across restarts)
    CREATE TABLE IF NOT EXISTS supervisor_pipeline_results (
        id TEXT PRIMARY KEY,
        pipeline_id TEXT NOT NULL UNIQUE,
        application_id TEXT NOT NULL,
        -- P12-5 / DCI-006: SUPERVISOR_PIPELINE_STATUS_VALUES (lockstep test)
        status TEXT NOT NULL DEFAULT 'running' CHECK(status IN (
            'running','completed','completed_with_errors','awaiting_review','failed'
        )),
        trigger_type TEXT,
        trigger_source TEXT,
        started_at TEXT,
        completed_at TEXT,
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_sup_pipeline_app ON supervisor_pipeline_results(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_pipeline_status ON supervisor_pipeline_results(status);
    CREATE INDEX IF NOT EXISTS idx_sup_pipeline_completed ON supervisor_pipeline_results(completed_at);

    -- Durable supervisor officer decisions (BSA-003B)
    CREATE TABLE IF NOT EXISTS supervisor_escalations (
        id TEXT PRIMARY KEY,
        pipeline_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        escalation_source TEXT NOT NULL,
        source_id TEXT,
        escalation_level TEXT NOT NULL,
        priority TEXT NOT NULL,
        reason TEXT NOT NULL,
        context_json TEXT DEFAULT '{}',
        assigned_to TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        sla_deadline TEXT,
        resolved_at TEXT,
        escalated_by_id TEXT NOT NULL,
        escalated_by_name TEXT NOT NULL,
        escalated_by_role TEXT NOT NULL,
        request_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_app ON supervisor_escalations(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_pipeline ON supervisor_escalations(pipeline_id);
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_status_created ON supervisor_escalations(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_sup_escalations_level_status ON supervisor_escalations(escalation_level, status);

    CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
        id TEXT PRIMARY KEY,
        pipeline_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        escalation_id TEXT REFERENCES supervisor_escalations(id),
        review_type TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        reviewer_name TEXT NOT NULL,
        reviewer_role TEXT NOT NULL,
        ai_recommendation TEXT,
        ai_confidence REAL,
        ai_risk_level TEXT,
        rules_recommendation TEXT,
        rules_triggered TEXT DEFAULT '[]',
        contradictions_json TEXT DEFAULT '[]',
        decision TEXT NOT NULL,
        decision_reason TEXT NOT NULL,
        risk_level_assigned TEXT,
        conditions TEXT,
        follow_up_required INTEGER NOT NULL DEFAULT 0,
        follow_up_details TEXT,
        is_ai_override INTEGER NOT NULL DEFAULT 0,
        override_reason TEXT,
        review_started_at TEXT,
        decision_at TEXT NOT NULL,
        request_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_app ON supervisor_human_reviews(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_pipeline ON supervisor_human_reviews(pipeline_id);
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_reviewer ON supervisor_human_reviews(reviewer_id);
    CREATE INDEX IF NOT EXISTS idx_sup_reviews_decision_at ON supervisor_human_reviews(decision_at);

    CREATE TABLE IF NOT EXISTS supervisor_overrides (
        id TEXT PRIMARY KEY,
        review_id TEXT NOT NULL REFERENCES supervisor_human_reviews(id),
        application_id TEXT NOT NULL,
        agent_type TEXT,
        override_type TEXT NOT NULL,
        original_value TEXT NOT NULL,
        override_value TEXT NOT NULL,
        reason TEXT NOT NULL,
        officer_id TEXT NOT NULL,
        officer_name TEXT NOT NULL,
        officer_role TEXT NOT NULL,
        approver_id TEXT,
        approver_name TEXT,
        approved_at TEXT,
        request_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sup_overrides_app ON supervisor_overrides(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_overrides_review ON supervisor_overrides(review_id);
    CREATE INDEX IF NOT EXISTS idx_sup_overrides_created ON supervisor_overrides(created_at);

    -- Supervisor audit log (production-grade, uses shared DB)
    CREATE TABLE IF NOT EXISTS supervisor_audit_log (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        -- P12-5 / DCI-006: SUPERVISOR_AUDIT_EVENT_TYPE_VALUES /
        -- SUPERVISOR_AUDIT_SEVERITY_VALUES (lockstep test).  The legacy-repair
        -- creator (_create_supervisor_audit_log_table) deliberately carries NO
        -- CHECKs so historical rows can be rehashed; v2.47 constrains PG after
        -- verifying the data is clean.
        event_type TEXT NOT NULL CHECK(event_type IN (
            'agent_run_started','agent_run_completed','agent_run_failed',
            'schema_validation_passed','schema_validation_failed',
            'confidence_calculated','confidence_routing',
            'contradiction_detected','contradiction_resolved',
            'rule_triggered','rule_overridden',
            'escalation_created','escalation_assigned','escalation_resolved',
            'human_review_started','human_review_completed','ai_override',
            'pipeline_started','pipeline_completed','pipeline_failed',
            'supervisor_verdict','config_changed',
            'agent_version_changed','prompt_version_changed','system_error'
        )),
        severity TEXT DEFAULT 'info' CHECK(severity IN (
            'critical','high','medium','low','info','warning'
        )),
        pipeline_id TEXT,
        application_id TEXT,
        run_id TEXT,
        agent_type TEXT,
        actor_type TEXT,
        actor_id TEXT,
        actor_name TEXT,
        actor_role TEXT,
        action TEXT NOT NULL,
        detail TEXT,
        data_json TEXT DEFAULT '{}',
        ip_address TEXT,
        session_id TEXT,
        previous_hash TEXT,
        entry_hash TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sup_audit_ts ON supervisor_audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_event ON supervisor_audit_log(event_type);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_app ON supervisor_audit_log(application_id);

    -- Decision records (normalized audit layer)
    CREATE TABLE IF NOT EXISTS decision_records (
        id TEXT PRIMARY KEY,
        application_ref TEXT NOT NULL,
        decision_type TEXT NOT NULL CHECK(decision_type IN (
            'approve','reject','escalate_edd','request_documents','pre_approve','request_info'
        )),
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        confidence_score REAL,
        source TEXT NOT NULL CHECK(source IN ('manual','supervisor','rule_engine')),
        actor_user_id TEXT,
        actor_role TEXT,
        timestamp TEXT NOT NULL,
        key_flags TEXT DEFAULT '[]',
        override_flag INTEGER DEFAULT 0,
        override_reason TEXT,
        extra_json TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_dec_rec_app ON decision_records(application_ref);
    CREATE INDEX IF NOT EXISTS idx_dec_rec_type ON decision_records(decision_type);
    CREATE INDEX IF NOT EXISTS idx_dec_rec_ts ON decision_records(timestamp);

    -- Screening Reports Normalized (Phase A4: dialect-safe DDL consolidated into init_db)
    -- IF NOT EXISTS guarantees existing production tables are untouched.
    CREATE TABLE IF NOT EXISTS screening_reports_normalized (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'sumsub',
        normalized_version TEXT NOT NULL DEFAULT '1.0',
        source_screening_report_hash TEXT,
        normalized_report_json TEXT,
        normalization_status TEXT NOT NULL DEFAULT 'success' CHECK(normalization_status IN ('success', 'failed')),
        normalization_error TEXT,
        is_authoritative INTEGER NOT NULL DEFAULT 0 CHECK(is_authoritative = 0),
        source TEXT NOT NULL DEFAULT 'migration_scaffolding',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_screening_normalized_client_app ON screening_reports_normalized(client_id, application_id);
    CREATE INDEX IF NOT EXISTS idx_screening_normalized_app_id ON screening_reports_normalized(application_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_normalized_app_provider_hash ON screening_reports_normalized(application_id, provider, source_screening_report_hash);

    -- D2 provider-pair comparison artifacts (Sumsub-primary / CA-shadow)
    CREATE TABLE IF NOT EXISTS screening_provider_comparisons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL,
        client_id TEXT NOT NULL,
        primary_provider TEXT NOT NULL,
        shadow_provider TEXT NOT NULL,
        comparison_kind TEXT NOT NULL DEFAULT 'screening_shadow',
        primary_normalized_record_id INTEGER,
        shadow_normalized_record_id INTEGER,
        mismatch_class TEXT NOT NULL,
        comparison_json TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_provider_comparisons_app ON screening_provider_comparisons(application_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_comparisons_app_pair ON screening_provider_comparisons(application_id, primary_provider, shadow_provider, comparison_kind);

    -- Screening Monitoring Subscriptions (Phase C1.a: ComplyAdvantage scaffolding)
    CREATE TABLE IF NOT EXISTS screening_monitoring_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        person_key TEXT,
        customer_identifier TEXT NOT NULL,
        external_subscription_id TEXT,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'paused', 'cancelled', 'expired')),
        subscribed_at TEXT DEFAULT (datetime('now')),
        last_event_at TEXT,
        last_webhook_type TEXT,
        monitoring_event_count INTEGER NOT NULL DEFAULT 0,
        is_authoritative INTEGER NOT NULL DEFAULT 0
            CHECK(is_authoritative = 0),
        source TEXT NOT NULL DEFAULT 'migration_scaffolding',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_screening_monitoring_subs_app
        ON screening_monitoring_subscriptions (application_id);
    CREATE INDEX IF NOT EXISTS idx_screening_monitoring_subs_client
        ON screening_monitoring_subscriptions (client_id, application_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_monitoring_subs_customer
        ON screening_monitoring_subscriptions (client_id, provider, customer_identifier);
    """


def log_agent_execution(
    application_id: str,
    agent_name: str,
    agent_number: int,
    status: str,
    checks: list = None,
    flags: list = None,
    requires_review: bool = False,
    source: str = "ai",
    document_id: str = None,
    started_at: str = None,
    error_message: str = None,
):
    """
    Log an agent execution to the agent_executions traceability table.
    Safe to call — silently fails if table doesn't exist yet.
    """
    db = None
    try:
        db = get_db()
        requires_review_value = bool(requires_review) if USE_POSTGRESQL else (1 if requires_review else 0)
        db.execute(
            """INSERT INTO agent_executions
               (application_id, document_id, agent_name, agent_number, status,
                checks_json, flags_json, requires_review, source, started_at, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                application_id,
                document_id,
                agent_name,
                agent_number,
                status,
                json.dumps(checks, default=str) if checks else None,
                json.dumps(flags, default=str) if flags else None,
                requires_review_value,
                source,
                started_at or datetime.now().isoformat(),
                error_message,
            )
        )
        db.commit()
    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.debug(f"Could not log agent execution: {e}")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def init_db():
    """Initialize database schema (creates tables if they don't exist)."""
    logger.info("startup: entering init_db (schema + inline migrations)")
    db = get_db()
    try:
        # BSA-003B hotfix: long-lived databases can already have the three
        # supervisor evidence tables with the pre-main-DB SQLite-era shape.
        # Reconcile them before the canonical schema runs because that schema
        # creates indexes on columns (notably decision_at) that legacy tables
        # do not have.  This same helper is repeated by inline v2.52 below so
        # the migration remains idempotent and self-contained.
        logger.info("startup: entering supervisor evidence schema preflight (v2.52)")
        _ensure_supervisor_human_review_persistence_schema(db)
        logger.info("startup: completed supervisor evidence schema preflight (v2.52)")

        if USE_POSTGRESQL:
            schema = _get_postgres_schema()
        else:
            schema = _get_sqlite_schema()
        logger.info("startup: executing schema DDL (%d chars)…", len(schema))
        db.executescript(schema)
        db.commit()
        logger.info("startup: schema DDL committed — Database schema initialized")

        # Fresh installs are already on the current schema because init_db()
        # creates it in one shot. Mark every known file migration as covered
        # before the file-based runner can replay legacy ALTER TABLE steps
        # against that modern schema (for example migration 014's status /
        # due_date additions to periodic_reviews).
        logger.info("startup: marking known file migrations as applied")
        _mark_known_migrations_as_applied(db)
        db.commit()

        # ── Migration: Add pre-approval columns if missing (v2.1) ──
        logger.info("startup: entering _run_migrations (inline)")
        _run_migrations(db)
        db.commit()
        logger.info("startup: completed _run_migrations (inline)")

        logger.info("startup: entering _ensure_company_registry_schema")
        _ensure_company_registry_schema(db)
        db.commit()
        logger.info("startup: completed _ensure_company_registry_schema")

        logger.info("startup: entering _apply_pr_cr1r_manual_score_defaults_once")
        _apply_pr_cr1r_manual_score_defaults_once(db)
        db.commit()
        logger.info("startup: completed _apply_pr_cr1r_manual_score_defaults_once")

        logger.info("startup: entering _ensure_supervisor_audit_log_schema")
        _ensure_supervisor_audit_log_schema(db)
        db.commit()
        logger.info("startup: completed _ensure_supervisor_audit_log_schema")

        # Migration v2.47 (P12-5 / DCI-006): enum CHECK constraints for
        # workflow status/source columns.  Runs AFTER the supervisor audit
        # schema repair so a rebuilt legacy table is constrained in the same
        # boot rather than the next one.
        logger.info("startup: entering _ensure_status_enum_constraints (v2.47)")
        _ensure_status_enum_constraints(db)
        db.commit()
        logger.info("startup: completed _ensure_status_enum_constraints (v2.47)")

        # Ensure built-in resources exist for the back-office reference library.
        logger.info("startup: entering _ensure_default_compliance_resources")
        _ensure_default_compliance_resources(db)
        db.commit()
        logger.info("startup: completed _ensure_default_compliance_resources")

        # Ensure system settings row exists for configuration-backed settings.
        logger.info("startup: entering _ensure_default_system_settings")
        _ensure_default_system_settings(db)
        db.commit()
        logger.info("startup: completed _ensure_default_system_settings")

        logger.info("startup: entering _ensure_country_risk_governance")
        _ensure_country_risk_governance(db)
        db.commit()
        logger.info("startup: completed _ensure_country_risk_governance")

        # Ensure configurable Enhanced / EDD requirement rules exist.
        logger.info("startup: entering _ensure_default_enhanced_requirement_rules")
        _ensure_default_enhanced_requirement_rules(db)
        db.commit()
        logger.info("startup: completed _ensure_default_enhanced_requirement_rules")

        # Ensure generated application-specific Enhanced / EDD requirements table exists.
        logger.info("startup: entering _ensure_application_enhanced_requirements_table")
        _ensure_application_enhanced_requirements_table(db)
        db.commit()
        logger.info("startup: completed _ensure_application_enhanced_requirements_table")

        # ── H-2: Ensure demo application stubs exist (run in init_db for reliability) ──
        # Use both config.IS_DEMO and environment.is_demo() for robustness
        _is_demo = _CFG_IS_DEMO
        try:
            from environment import is_demo as _env_is_demo
            _is_demo = _is_demo or _env_is_demo()
        except ImportError:
            pass
        if _is_demo:
            try:
                for app_id, ref, company in [
                    ("demo-scenario-01", "ARF-2026-DEMO01", "Meridian Software Ltd"),
                    ("demo-scenario-02", "ARF-2026-DEMO02", "Coral Bay Holdings Ltd"),
                    ("demo-scenario-03", "ARF-2026-DEMO03", "Atlas Digital Assets DMCC"),
                    ("demo-scenario-04", "ARF-2026-DEMO04", "Sunshine Trading Co"),
                    ("demo-scenario-05", "ARF-2026-DEMO05", "Levant Global Enterprises S.A.L."),
                ]:
                    db.execute(
                        "INSERT OR IGNORE INTO applications (id, ref, company_name, status) VALUES (?, ?, ?, 'submitted')",
                        (app_id, ref, company)
                    )
                db.commit()
                logger.info("Demo application stubs ensured in init_db")
            except Exception as e:
                logger.warning(f"Demo app stubs in init_db skipped: {e}")


    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise
    finally:
        db.close()


def _mark_known_migrations_as_applied(db: DBConnection):
    """On fresh installs, record file migrations already represented by init_db.

    ``init_db`` creates the complete current schema, so the file-based
    migration runner must treat every existing schema migration file as already
    applied. Data migrations listed in ``FILE_MIGRATIONS_REQUIRING_RUNNER`` are
    not represented by the DDL and must still run through the file runner.
    Long-lived databases keep their existing rows and only missing versions are
    inserted here.
    """
    from migrations.runner import MIGRATIONS_DIR, ensure_schema_version_table

    ensure_schema_version_table(db)

    for path in sorted(MIGRATIONS_DIR.glob("migration_*.sql")):
        parts = path.stem.split("_", 2)
        if len(parts) < 2:
            continue
        version = parts[1]
        if version in FILE_MIGRATIONS_REQUIRING_RUNNER:
            if version == "040" and _dsar_erasure_truth_columns_present(db):
                existing = db.execute(
                    "SELECT 1 FROM schema_version WHERE version = ?",
                    (version,),
                ).fetchone()
                if existing is None:
                    db.execute(
                        "INSERT INTO schema_version (version, filename, description, checksum) "
                        "VALUES (?, ?, ?, ?)",
                        (version, path.name, "covered by init_db", "init_db"),
                    )
                continue
            # Repair prior deploys that incorrectly pre-marked a data migration
            # as "covered by init_db". A genuinely applied file migration has
            # its file checksum, so it is left untouched.
            db.execute(
                "DELETE FROM schema_version "
                "WHERE version = ? AND filename = ? "
                "AND description = ? AND checksum = ?",
                (version, path.name, "covered by init_db", "init_db"),
            )
            continue
        existing = db.execute(
            "SELECT 1 FROM schema_version WHERE version = ?",
            (version,),
        ).fetchone()
        if existing is not None:
            continue
        db.execute(
            "INSERT INTO schema_version (version, filename, description, checksum) "
            "VALUES (?, ?, ?, ?)",
            (version, path.name, "covered by init_db", "init_db"),
        )
    db.commit()


def _dsar_erasure_truth_columns_present(db: DBConnection) -> bool:
    """Return whether data_subject_requests already carries H2A truth columns."""
    try:
        return all(
            _safe_column_exists(db, "data_subject_requests", column)
            for column in DSAR_ERASURE_TRUTH_COLUMNS
        )
    except Exception:
        return False


def _ensure_default_compliance_resources(db: DBConnection):
    """Seed a small internal resource library with real files already present in the repo."""
    repo_root = Path(__file__).resolve().parent.parent
    default_resources = [
        {
            "slug": "risk-score-sheet",
            "title": "ARIE Risk Score Sheet",
            "description": "Excel workbook with the current risk scoring matrix and supporting dimensions.",
            "category": "internal",
            "resource_type": "system",
            "path": repo_root / "docs" / "compliance" / "ARIE_Risk_Score_Sheet.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
        {
            "slug": "regulatory-compliance-report",
            "title": "Onboarda Regulatory Compliance Report",
            "description": "Internal compliance reference report for regulatory obligations and remediation context.",
            "category": "internal",
            "resource_type": "system",
            "path": repo_root / "docs" / "compliance" / "Onboarda_Regulatory_Compliance_Report.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
        {
            "slug": "sample-compliance-memo",
            "title": "Onboarda Sample Compliance Memo",
            "description": "Reference memo format used by officers when reviewing onboarding decisions.",
            "category": "internal",
            "resource_type": "system",
            "path": repo_root / "docs" / "compliance" / "Onboarda_Sample_Compliance_Memo.pdf",
            "mime_type": "application/pdf",
        },
    ]

    for resource in default_resources:
        path = resource["path"]
        if not path.exists():
            logger.warning("Skipping default compliance resource because file is missing: %s", path)
            continue
        try:
            db.execute(
                """
                INSERT OR IGNORE INTO compliance_resources
                (slug, title, description, category, resource_type, file_name, file_path, mime_type, file_size)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    resource["slug"],
                    resource["title"],
                    resource["description"],
                    resource["category"],
                    resource["resource_type"],
                    path.name,
                    str(path),
                    resource["mime_type"],
                    path.stat().st_size,
                ),
            )
        except Exception as e:
            logger.debug("Default compliance resource seed skipped for %s: %s", resource["slug"], e)


def _ensure_default_system_settings(db: DBConnection):
    """Seed default system settings row for the back-office settings view."""
    try:
        db.execute("""
            INSERT OR IGNORE INTO system_settings
            (id, company_name, licence_number, default_retention_years, auto_approve_max_score, edd_threshold_score)
            VALUES (1,?,?,?,?,?)
        """, (
            "Onboarda Ltd",
            "FSC-PIS-2024-001",
            7,
            40,
            55,
        ))
    except Exception as e:
        logger.debug("Default system settings seed skipped: %s", e)


def _ensure_enhanced_requirement_rules_table(db: DBConnection):
    """Create the enhanced requirement rules table for existing databases."""
    if db.is_postgres:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS enhanced_requirement_rules (
            id SERIAL PRIMARY KEY,
            trigger_key TEXT NOT NULL,
            trigger_label TEXT NOT NULL,
            trigger_category TEXT NOT NULL DEFAULT 'risk',
            requirement_key TEXT NOT NULL,
            requirement_label TEXT NOT NULL,
            requirement_description TEXT,
            audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
            requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
            subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
            blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
            waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
            waiver_roles JSONB DEFAULT '[]',
            mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            sort_order INTEGER NOT NULL DEFAULT 100,
            applies_when JSONB DEFAULT '{}',
            client_safe_label TEXT,
            client_safe_description TEXT,
            internal_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT REFERENCES users(id),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT REFERENCES users(id),
            UNIQUE(trigger_key, requirement_key)
        );
        CREATE INDEX IF NOT EXISTS idx_enhanced_req_trigger ON enhanced_requirement_rules(trigger_key);
        CREATE INDEX IF NOT EXISTS idx_enhanced_req_active ON enhanced_requirement_rules(active);
        CREATE INDEX IF NOT EXISTS idx_enhanced_req_audience ON enhanced_requirement_rules(audience);
        """)
    else:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS enhanced_requirement_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_key TEXT NOT NULL,
            trigger_label TEXT NOT NULL,
            trigger_category TEXT NOT NULL DEFAULT 'risk',
            requirement_key TEXT NOT NULL,
            requirement_label TEXT NOT NULL,
            requirement_description TEXT,
            audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
            requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
            subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
            blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
            waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
            waiver_roles TEXT DEFAULT '[]',
            mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            sort_order INTEGER NOT NULL DEFAULT 100,
            applies_when TEXT DEFAULT '{}',
            client_safe_label TEXT,
            client_safe_description TEXT,
            internal_notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            created_by TEXT REFERENCES users(id),
            updated_at TEXT DEFAULT (datetime('now')),
            updated_by TEXT REFERENCES users(id),
            UNIQUE(trigger_key, requirement_key)
        );
        CREATE INDEX IF NOT EXISTS idx_enhanced_req_trigger ON enhanced_requirement_rules(trigger_key);
        CREATE INDEX IF NOT EXISTS idx_enhanced_req_active ON enhanced_requirement_rules(active);
        CREATE INDEX IF NOT EXISTS idx_enhanced_req_audience ON enhanced_requirement_rules(audience);
        """)


def _ensure_application_enhanced_requirements_table(db: DBConnection):
    """Create generated application Enhanced / EDD requirements table."""
    if db.is_postgres:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS application_enhanced_requirements (
            id SERIAL PRIMARY KEY,
            application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            source_rule_id INTEGER REFERENCES enhanced_requirement_rules(id),
            trigger_key TEXT NOT NULL,
            trigger_label TEXT NOT NULL,
            trigger_category TEXT NOT NULL DEFAULT 'risk',
            requirement_key TEXT NOT NULL,
            requirement_label TEXT NOT NULL,
            requirement_description TEXT,
            audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
            requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
            subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
            blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
            waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
            waiver_roles TEXT DEFAULT '[]',
            mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
            status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN ('generated','requested','uploaded','under_review','accepted','rejected','waived','cancelled')),
            generation_source TEXT NOT NULL DEFAULT 'manual_api',
            trigger_reason TEXT,
            trigger_context TEXT DEFAULT '{}',
            linked_periodic_review_id INTEGER REFERENCES periodic_reviews(id) ON DELETE SET NULL,
            linked_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
            monitoring_alert_id INTEGER,
            monitoring_document_id TEXT,
            due_date TIMESTAMP,
            linked_rmi_item_id TEXT,
            requested_at TIMESTAMP,
            requested_by TEXT REFERENCES users(id),
            uploaded_at TIMESTAMP,
            client_response_text TEXT,
            client_response_at TIMESTAMP,
            client_response_by TEXT REFERENCES clients(id),
            reviewed_at TIMESTAMP,
            reviewed_by TEXT REFERENCES users(id),
            review_notes TEXT,
            workflow_test_accepted BOOLEAN DEFAULT FALSE,
            workflow_test_acceptance_reason TEXT,
            workflow_test_accepted_by TEXT REFERENCES users(id),
            workflow_test_accepted_at TIMESTAMP,
            workflow_test_acceptance_environment TEXT,
            workflow_test_acceptance_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
            waived_at TIMESTAMP,
            waived_by TEXT REFERENCES users(id),
            waiver_reason TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT REFERENCES users(id),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT REFERENCES users(id),
            UNIQUE(application_id, trigger_key, requirement_key)
        );
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_app ON application_enhanced_requirements(application_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_rule ON application_enhanced_requirements(source_rule_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_trigger ON application_enhanced_requirements(trigger_key);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_linked_review ON application_enhanced_requirements(linked_periodic_review_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_status ON application_enhanced_requirements(status);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_active ON application_enhanced_requirements(active);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_alert ON application_enhanced_requirements(monitoring_alert_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_doc ON application_enhanced_requirements(monitoring_document_id);
        """)
    else:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS application_enhanced_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            source_rule_id INTEGER REFERENCES enhanced_requirement_rules(id),
            trigger_key TEXT NOT NULL,
            trigger_label TEXT NOT NULL,
            trigger_category TEXT NOT NULL DEFAULT 'risk',
            requirement_key TEXT NOT NULL,
            requirement_label TEXT NOT NULL,
            requirement_description TEXT,
            audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
            requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
            subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
            blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
            waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
            waiver_roles TEXT DEFAULT '[]',
            mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
            status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN ('generated','requested','uploaded','under_review','accepted','rejected','waived','cancelled')),
            generation_source TEXT NOT NULL DEFAULT 'manual_api',
            trigger_reason TEXT,
            trigger_context TEXT DEFAULT '{}',
            linked_periodic_review_id INTEGER REFERENCES periodic_reviews(id) ON DELETE SET NULL,
            linked_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
            monitoring_alert_id INTEGER,
            monitoring_document_id TEXT,
            due_date TEXT,
            linked_rmi_item_id TEXT,
            requested_at TEXT,
            requested_by TEXT REFERENCES users(id),
            uploaded_at TEXT,
            client_response_text TEXT,
            client_response_at TEXT,
            client_response_by TEXT REFERENCES clients(id),
            reviewed_at TEXT,
            reviewed_by TEXT REFERENCES users(id),
            review_notes TEXT,
            workflow_test_accepted INTEGER DEFAULT 0,
            workflow_test_acceptance_reason TEXT,
            workflow_test_accepted_by TEXT REFERENCES users(id),
            workflow_test_accepted_at TEXT,
            workflow_test_acceptance_environment TEXT,
            workflow_test_acceptance_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
            waived_at TEXT,
            waived_by TEXT REFERENCES users(id),
            waiver_reason TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TEXT DEFAULT (datetime('now')),
            created_by TEXT REFERENCES users(id),
            updated_at TEXT DEFAULT (datetime('now')),
            updated_by TEXT REFERENCES users(id),
            UNIQUE(application_id, trigger_key, requirement_key)
        );
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_app ON application_enhanced_requirements(application_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_rule ON application_enhanced_requirements(source_rule_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_trigger ON application_enhanced_requirements(trigger_key);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_linked_review ON application_enhanced_requirements(linked_periodic_review_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_status ON application_enhanced_requirements(status);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_active ON application_enhanced_requirements(active);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_alert ON application_enhanced_requirements(monitoring_alert_id);
        CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_doc ON application_enhanced_requirements(monitoring_document_id);
        """)
    _ensure_application_enhanced_requirement_fulfilment_columns(db)


def _ensure_application_enhanced_requirement_fulfilment_columns(db: DBConnection):
    """Add Step 5C client fulfilment fields without changing existing rows."""
    if not _safe_table_exists(db, "application_enhanced_requirements"):
        return
    column_types = {
        "client_response_text": "TEXT",
        "client_response_at": "TIMESTAMP" if db.is_postgres else "TEXT",
        "client_response_by": "TEXT REFERENCES clients(id)",
        "workflow_test_accepted": "BOOLEAN DEFAULT FALSE" if db.is_postgres else "INTEGER DEFAULT 0",
        "workflow_test_acceptance_reason": "TEXT",
        "workflow_test_accepted_by": "TEXT REFERENCES users(id)",
        "workflow_test_accepted_at": "TIMESTAMP" if db.is_postgres else "TEXT",
        "workflow_test_acceptance_environment": "TEXT",
        "workflow_test_acceptance_document_id": "TEXT REFERENCES documents(id) ON DELETE SET NULL",
        "linked_periodic_review_id": "INTEGER REFERENCES periodic_reviews(id) ON DELETE SET NULL",
        "monitoring_alert_id": "INTEGER",
        "monitoring_document_id": "TEXT",
        "due_date": "TIMESTAMP" if db.is_postgres else "TEXT",
    }
    for column, definition in column_types.items():
        if not _safe_column_exists(db, "application_enhanced_requirements", column):
            db.execute(
                f"ALTER TABLE application_enhanced_requirements ADD COLUMN {column} {definition}"
            )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_linked_review "
        "ON application_enhanced_requirements(linked_periodic_review_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_alert "
        "ON application_enhanced_requirements(monitoring_alert_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_doc "
        "ON application_enhanced_requirements(monitoring_document_id)"
    )


def _ensure_default_enhanced_requirement_rules(db: DBConnection):
    """Seed default Enhanced / EDD requirement rules idempotently."""
    try:
        _ensure_enhanced_requirement_rules_table(db)
        from enhanced_requirements import seed_default_enhanced_requirement_rules
        seed_default_enhanced_requirement_rules(db)
    except Exception as e:
        logger.warning("Default enhanced requirement rules seed skipped: %s", e)


def _safe_column_exists(db: DBConnection, table: str, column: str) -> bool:
    """Check if a column exists without aborting the PostgreSQL transaction.

    On PostgreSQL a failed ``SELECT col`` puts the connection into an error
    state, so we use ``information_schema`` instead.  SQLite falls back to a
    PRAGMA lookup.
    """
    if db.is_postgres:
        row = db.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            (table, column),
        ).fetchone()
        return row is not None
    else:
        try:
            db.execute(f"SELECT {column} FROM {table} LIMIT 1")
            return True
        except Exception:
            return False


def _safe_table_exists(db: DBConnection, table: str) -> bool:
    """Check if a table exists without aborting the PostgreSQL transaction."""
    if db.is_postgres:
        row = db.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = ?",
            (table,),
        ).fetchone()
        return row is not None
    else:
        try:
            db.execute(f"SELECT 1 FROM {table} LIMIT 1")
            return True
        except Exception:
            return False


def _supervisor_column_metadata(db: DBConnection, table: str, column: str):
    """Return dialect-neutral metadata for a known supervisor column."""
    if db.is_postgres:
        return db.execute(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = ? AND column_name = ?",
            (table, column),
        ).fetchone()

    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    for row in rows:
        if row["name"] == column:
            return {
                "data_type": str(row["type"] or "").lower(),
                "is_nullable": "NO" if row["notnull"] else "YES",
                "column_default": row["dflt_value"],
                "is_primary_key": bool(row["pk"]),
            }
    return None


def _ensure_supervisor_text_id(db: DBConnection, table: str) -> None:
    """Make a legacy supervisor integer key accept #746 UUID text values."""
    if not _safe_table_exists(db, table):
        return
    id_meta = _supervisor_column_metadata(db, table, "id")
    if not id_meta or str(id_meta["data_type"]).lower() in (
        "text", "character varying", "varchar"
    ):
        return

    if db.is_postgres:
        db.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
        db.execute(f"ALTER TABLE {table} ALTER COLUMN id TYPE TEXT USING id::text")
        return

    if not _safe_column_exists(db, table, "legacy_id"):
        db.execute(f"ALTER TABLE {table} RENAME COLUMN id TO legacy_id")
        db.execute(f"ALTER TABLE {table} ADD COLUMN id TEXT")
        db.execute(f"UPDATE {table} SET id = CAST(legacy_id AS TEXT)")
        db.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_text_id "
            f"ON {table}(id)"
        )


def _rebuild_sqlite_legacy_supervisor_overrides(db: DBConnection) -> None:
    """Remove the obsolete SQLite pipeline_id write gate without data loss.

    SQLite cannot drop a NOT NULL constraint in place.  Rebuild only this
    legacy test/dev table, retaining its old evidence columns as nullable
    compatibility fields while adding the #746 runtime contract.  PostgreSQL
    uses an in-place ``DROP NOT NULL`` and never enters this path.
    """
    legacy_table = "supervisor_overrides_bsa003_legacy"
    if _safe_table_exists(db, legacy_table):
        raise RuntimeError(
            "incomplete prior supervisor_overrides SQLite reconciliation"
        )

    db.execute(f"ALTER TABLE supervisor_overrides RENAME TO {legacy_table}")
    db.execute(
        """
        CREATE TABLE supervisor_overrides (
            id TEXT PRIMARY KEY,
            review_id TEXT,
            application_id TEXT NOT NULL,
            agent_type TEXT,
            override_type TEXT NOT NULL,
            original_value TEXT,
            override_value TEXT,
            reason TEXT NOT NULL,
            officer_id TEXT NOT NULL,
            officer_name TEXT,
            officer_role TEXT,
            approver_id TEXT,
            approver_name TEXT,
            approved_at TEXT,
            request_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            pipeline_id TEXT,
            ai_recommendation TEXT,
            officer_decision TEXT
        )
        """
    )
    db.execute(
        f"""
        INSERT INTO supervisor_overrides
            (id, application_id, override_type, original_value,
             override_value, reason, officer_id, officer_name, created_at,
             pipeline_id, ai_recommendation, officer_decision)
        SELECT CAST(id AS TEXT), application_id, override_type,
               ai_recommendation, officer_decision, reason, officer_id,
               officer_name, created_at, pipeline_id, ai_recommendation,
               officer_decision
        FROM {legacy_table}
        """
    )
    db.execute(f"DROP TABLE {legacy_table}")


def _ensure_supervisor_human_review_persistence_schema(db: DBConnection) -> None:
    """Reconcile BSA-003B evidence tables before creating their indexes.

    The old local-persistence DDL was also present in some long-lived main
    databases.  ``CREATE TABLE IF NOT EXISTS`` cannot upgrade those tables, so
    the canonical schema used to fail while creating the first new-column
    index.  Keep this repair additive: preserve legacy columns/rows, add the
    runtime contract, and create indexes only after every referenced column is
    known to exist.
    """
    timestamp_type = "TIMESTAMP" if db.is_postgres else "TEXT"

    # Repair the parent key types before CREATE TABLE can introduce a missing
    # child table with a text foreign key.  This also supports partially
    # present legacy schemas, not only the all-three-tables staging shape.
    for table in (
        "supervisor_escalations",
        "supervisor_human_reviews",
        "supervisor_overrides",
    ):
        _ensure_supervisor_text_id(db, table)

    # Step 1: ensure all three tables exist.  Fresh databases receive the
    # strict canonical contract; existing legacy tables are left intact for
    # the additive reconciliation below.
    db.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS supervisor_escalations (
            id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            escalation_source TEXT NOT NULL,
            source_id TEXT,
            escalation_level TEXT NOT NULL,
            priority TEXT NOT NULL,
            reason TEXT NOT NULL,
            context_json TEXT DEFAULT '{{}}',
            assigned_to TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            sla_deadline {timestamp_type},
            resolved_at {timestamp_type},
            escalated_by_id TEXT NOT NULL,
            escalated_by_name TEXT NOT NULL,
            escalated_by_role TEXT NOT NULL,
            request_id TEXT,
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
            id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            escalation_id TEXT REFERENCES supervisor_escalations(id),
            review_type TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            reviewer_name TEXT NOT NULL,
            reviewer_role TEXT NOT NULL,
            ai_recommendation TEXT,
            ai_confidence REAL,
            ai_risk_level TEXT,
            rules_recommendation TEXT,
            rules_triggered TEXT DEFAULT '[]',
            contradictions_json TEXT DEFAULT '[]',
            decision TEXT NOT NULL,
            decision_reason TEXT NOT NULL,
            risk_level_assigned TEXT,
            conditions TEXT,
            follow_up_required INTEGER NOT NULL DEFAULT 0,
            follow_up_details TEXT,
            is_ai_override INTEGER NOT NULL DEFAULT 0,
            override_reason TEXT,
            review_started_at {timestamp_type},
            decision_at {timestamp_type} NOT NULL,
            request_id TEXT,
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS supervisor_overrides (
            id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL REFERENCES supervisor_human_reviews(id),
            application_id TEXT NOT NULL,
            agent_type TEXT,
            override_type TEXT NOT NULL,
            original_value TEXT NOT NULL,
            override_value TEXT NOT NULL,
            reason TEXT NOT NULL,
            officer_id TEXT NOT NULL,
            officer_name TEXT NOT NULL,
            officer_role TEXT NOT NULL,
            approver_id TEXT,
            approver_name TEXT,
            approved_at {timestamp_type},
            request_id TEXT,
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    column_definitions = {
        "supervisor_escalations": {
            "pipeline_id": "TEXT",
            "application_id": "TEXT",
            "escalation_source": "TEXT",
            "source_id": "TEXT",
            "escalation_level": "TEXT",
            "priority": "TEXT",
            "reason": "TEXT",
            "context_json": "TEXT DEFAULT '{}'",
            "assigned_to": "TEXT",
            "status": "TEXT DEFAULT 'pending'",
            "sla_deadline": timestamp_type,
            "resolved_at": timestamp_type,
            "escalated_by_id": "TEXT",
            "escalated_by_name": "TEXT",
            "escalated_by_role": "TEXT",
            "request_id": "TEXT",
            "created_at": timestamp_type,
        },
        "supervisor_human_reviews": {
            "pipeline_id": "TEXT",
            "application_id": "TEXT",
            "escalation_id": "TEXT",
            "review_type": "TEXT",
            "reviewer_id": "TEXT",
            "reviewer_name": "TEXT",
            "reviewer_role": "TEXT",
            "ai_recommendation": "TEXT",
            "ai_confidence": "REAL",
            "ai_risk_level": "TEXT",
            "rules_recommendation": "TEXT",
            "rules_triggered": "TEXT DEFAULT '[]'",
            "contradictions_json": "TEXT DEFAULT '[]'",
            "decision": "TEXT",
            "decision_reason": "TEXT",
            "risk_level_assigned": "TEXT",
            "conditions": "TEXT",
            "follow_up_required": "INTEGER DEFAULT 0",
            "follow_up_details": "TEXT",
            "is_ai_override": "INTEGER DEFAULT 0",
            "override_reason": "TEXT",
            "review_started_at": timestamp_type,
            "decision_at": timestamp_type,
            "request_id": "TEXT",
            "created_at": timestamp_type,
        },
        "supervisor_overrides": {
            "review_id": "TEXT",
            "application_id": "TEXT",
            "agent_type": "TEXT",
            "override_type": "TEXT",
            "original_value": "TEXT",
            "override_value": "TEXT",
            "reason": "TEXT",
            "officer_id": "TEXT",
            "officer_name": "TEXT",
            "officer_role": "TEXT",
            "approver_id": "TEXT",
            "approver_name": "TEXT",
            "approved_at": timestamp_type,
            "request_id": "TEXT",
            "created_at": timestamp_type,
        },
    }

    # SQLite cannot relax the one obsolete legacy NOT NULL constraint in
    # place.  Rebuild that table before the general additive pass; this path is
    # restricted to SQLite test/dev databases and preserves every legacy
    # evidence field.
    if not db.is_postgres:
        legacy_pipeline_meta = _supervisor_column_metadata(
            db, "supervisor_overrides", "pipeline_id"
        )
        if legacy_pipeline_meta and legacy_pipeline_meta["is_nullable"] == "NO":
            _rebuild_sqlite_legacy_supervisor_overrides(db)

    # The legacy tables used INTEGER PRIMARY KEY while #746 writes UUID text.
    # A pre-create pass above handles existing parent tables before foreign
    # keys are introduced; repeat here for idempotency and newly created tables.
    for table in column_definitions:
        _ensure_supervisor_text_id(db, table)

        for column, definition in column_definitions[table].items():
            if not _safe_column_exists(db, table, column):
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # The legacy override shape required pipeline_id even though #746's
    # override record is review/application scoped and does not write it.
    # Preserve the legacy value while removing only that obsolete write gate.
    pipeline_meta = _supervisor_column_metadata(
        db, "supervisor_overrides", "pipeline_id"
    )
    if pipeline_meta and pipeline_meta["is_nullable"] == "NO":
        if db.is_postgres:
            db.execute(
                "ALTER TABLE supervisor_overrides "
                "ALTER COLUMN pipeline_id DROP NOT NULL"
            )
        else:
            # The SQLite rebuild above must have removed this constraint.
            raise RuntimeError(
                "legacy supervisor_overrides.pipeline_id remains NOT NULL"
            )

    # Preserve the legacy timestamp/override meaning where those predecessor
    # columns exist.  Fresh tables and new writes already provide these values.
    if _safe_column_exists(db, "supervisor_human_reviews", "reviewed_at"):
        reviewed_at_value = (
            "reviewed_at::timestamptz" if db.is_postgres else "reviewed_at"
        )
        db.execute(
            "UPDATE supervisor_human_reviews "
            f"SET decision_at = COALESCE(decision_at, {reviewed_at_value}, CURRENT_TIMESTAMP), "
            f"created_at = COALESCE(created_at, {reviewed_at_value}, CURRENT_TIMESTAMP) "
            "WHERE decision_at IS NULL OR created_at IS NULL"
        )
    else:
        db.execute(
            "UPDATE supervisor_human_reviews "
            "SET decision_at = COALESCE(decision_at, CURRENT_TIMESTAMP), "
            "created_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
            "WHERE decision_at IS NULL OR created_at IS NULL"
        )
    if _safe_column_exists(db, "supervisor_human_reviews", "override_ai"):
        db.execute(
            "UPDATE supervisor_human_reviews "
            "SET is_ai_override = COALESCE(is_ai_override, override_ai, 0) "
            "WHERE is_ai_override IS NULL"
        )

    # Step 3: indexes are deliberately last.  Every indexed column above has
    # now been verified or added for both fresh and legacy databases.
    db.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_sup_escalations_app ON supervisor_escalations(application_id);
        CREATE INDEX IF NOT EXISTS idx_sup_escalations_pipeline ON supervisor_escalations(pipeline_id);
        CREATE INDEX IF NOT EXISTS idx_sup_escalations_status_created ON supervisor_escalations(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_sup_escalations_level_status ON supervisor_escalations(escalation_level, status);
        CREATE INDEX IF NOT EXISTS idx_sup_reviews_app ON supervisor_human_reviews(application_id);
        CREATE INDEX IF NOT EXISTS idx_sup_reviews_pipeline ON supervisor_human_reviews(pipeline_id);
        CREATE INDEX IF NOT EXISTS idx_sup_reviews_reviewer ON supervisor_human_reviews(reviewer_id);
        CREATE INDEX IF NOT EXISTS idx_sup_reviews_decision_at ON supervisor_human_reviews(decision_at);
        CREATE INDEX IF NOT EXISTS idx_sup_overrides_app ON supervisor_overrides(application_id);
        CREATE INDEX IF NOT EXISTS idx_sup_overrides_review ON supervisor_overrides(review_id);
        CREATE INDEX IF NOT EXISTS idx_sup_overrides_created ON supervisor_overrides(created_at);
        """
    )


def _ensure_company_registry_schema(db: DBConnection) -> None:
    """Create company registry evidence/cache tables and party provenance columns.

    PR-CH-INTAKE-2 keeps applications, directors, and UBOs as the source of
    truth. The intake session table is intentionally thin: it tracks progress
    and links to a registry lookup but does not duplicate confirmed parties.
    """
    json_type = "JSONB" if db.is_postgres else "TEXT"
    timestamp_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    bool_type = "BOOLEAN" if db.is_postgres else "INTEGER"
    bool_default_false = "FALSE" if db.is_postgres else "0"
    default_now = "CURRENT_TIMESTAMP" if db.is_postgres else "(datetime('now'))"
    id_default = "encode(gen_random_bytes(8), 'hex')" if db.is_postgres else "(lower(hex(randomblob(8))))"

    db.executescript(f"""
    CREATE TABLE IF NOT EXISTS company_registry_lookups (
        id TEXT PRIMARY KEY DEFAULT {id_default},
        provider TEXT NOT NULL,
        jurisdiction TEXT,
        company_number TEXT,
        query TEXT,
        result_type TEXT,
        raw_response_json {json_type},
        normalized_json {json_type},
        response_hash TEXT,
        fetched_at {timestamp_type},
        fetched_by TEXT,
        application_id TEXT REFERENCES applications(id) ON DELETE SET NULL,
        status TEXT,
        error_code TEXT,
        source_endpoint TEXT,
        simulation_used {bool_type} DEFAULT {bool_default_false},
        created_at {timestamp_type} DEFAULT {default_now},
        updated_at {timestamp_type} DEFAULT {default_now}
    );

    CREATE INDEX IF NOT EXISTS idx_company_registry_lookups_provider_company
        ON company_registry_lookups(provider, jurisdiction, company_number, result_type);
    CREATE INDEX IF NOT EXISTS idx_company_registry_lookups_application
        ON company_registry_lookups(application_id);
    CREATE INDEX IF NOT EXISTS idx_company_registry_lookups_response_hash
        ON company_registry_lookups(response_hash);

    CREATE TABLE IF NOT EXISTS company_intake_sessions (
        id TEXT PRIMARY KEY DEFAULT {id_default},
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        client_user_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        registry_lookup_id TEXT REFERENCES company_registry_lookups(id) ON DELETE SET NULL,
        country_of_incorporation TEXT,
        provider TEXT,
        company_number TEXT,
        stage TEXT,
        completion_score REAL DEFAULT 0,
        missing_answers_json {json_type} DEFAULT '[]',
        document_checklist_json {json_type} DEFAULT '[]',
        created_at {timestamp_type} DEFAULT {default_now},
        updated_at {timestamp_type} DEFAULT {default_now}
    );

    CREATE INDEX IF NOT EXISTS idx_company_intake_sessions_client
        ON company_intake_sessions(client_user_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_company_intake_sessions_application
        ON company_intake_sessions(application_id);
    CREATE INDEX IF NOT EXISTS idx_company_intake_sessions_registry_lookup
        ON company_intake_sessions(registry_lookup_id);
    """)

    director_columns = {
        "country_of_residence": "TEXT",
        "residential_address": "TEXT",
        "date_of_appointment": "TEXT",
        "source": "TEXT",
        "officer_role": "TEXT",
        "officer_entity_type": "TEXT",
        "requires_individual_kyc": bool_type,
        "requires_corporate_structure_review": bool_type,
        "registry_lookup_id": "TEXT",
        "response_hash": "TEXT",
        "source_metadata_json": json_type,
        "imported_at": timestamp_type,
        "imported_by": "TEXT",
    }
    ubo_columns = {
        "country_of_residence": "TEXT",
        "residential_address": "TEXT",
        "source": "TEXT",
        "psc_state": "TEXT",
        "registry_statement_type": "TEXT",
        "psc_status_reason": "TEXT",
        "psc_kind": "TEXT",
        "is_candidate_ubo": bool_type,
        "registry_lookup_id": "TEXT",
        "response_hash": "TEXT",
        "source_metadata_json": json_type,
        "imported_at": timestamp_type,
        "imported_by": "TEXT",
    }
    intermediary_columns = {
        "registration_number": "TEXT",
        "registered_address": "TEXT",
        "ownership_pct": "REAL",
        "owned_or_controlled_by": "TEXT",
        "source": "TEXT",
        "psc_state": "TEXT",
        "psc_kind": "TEXT",
        "is_candidate_intermediary": bool_type,
        "requires_corporate_structure_review": bool_type,
        "registry_lookup_id": "TEXT",
        "response_hash": "TEXT",
        "source_metadata_json": json_type,
        "imported_at": timestamp_type,
        "imported_by": "TEXT",
    }
    for table_name, columns in (("directors", director_columns), ("ubos", ubo_columns), ("intermediaries", intermediary_columns)):
        if not _safe_table_exists(db, table_name):
            continue
        for column_name, column_type in columns.items():
            if not _safe_column_exists(db, table_name, column_name):
                db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    db.execute("CREATE INDEX IF NOT EXISTS idx_directors_registry_lookup ON directors(registry_lookup_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ubos_registry_lookup ON ubos(registry_lookup_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intermediaries_registry_lookup ON intermediaries(registry_lookup_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_directors_source_person ON directors(application_id, source, person_key)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ubos_source_person ON ubos(application_id, source, person_key)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intermediaries_source_person ON intermediaries(application_id, source, person_key)")


def _ensure_country_risk_governance(db: DBConnection):
    """Create dormant country-risk snapshot tables.

    PR-CR1R restores manual risk_config.country_risk_scores as the active pilot
    source of truth. Snapshot tables remain for future remediation but are not
    seeded or used operationally.
    """
    db.executescript("""
    CREATE TABLE IF NOT EXISTS country_risk_snapshots (
        id TEXT PRIMARY KEY,
        version TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        source_name TEXT NOT NULL,
        source_url TEXT,
        source_publication_date TEXT,
        effective_date TEXT NOT NULL,
        imported_at TEXT DEFAULT (datetime('now')),
        imported_by TEXT NOT NULL DEFAULT 'system',
        last_checked_at TEXT DEFAULT (datetime('now')),
        checksum TEXT NOT NULL,
        freshness_days INTEGER NOT NULL DEFAULT 180,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS country_risk_entries (
        id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL REFERENCES country_risk_snapshots(id),
        country_name TEXT NOT NULL,
        country_key TEXT NOT NULL,
        iso_alpha2 TEXT,
        iso_alpha3 TEXT,
        risk_rating TEXT NOT NULL,
        risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 1 AND 4),
        fatf_status TEXT NOT NULL DEFAULT 'none',
        sanctions_status TEXT NOT NULL DEFAULT 'none',
        high_risk_status TEXT NOT NULL DEFAULT 'none',
        source_name TEXT NOT NULL,
        source_url TEXT,
        source_publication_date TEXT,
        effective_date TEXT NOT NULL,
        imported_at TEXT DEFAULT (datetime('now')),
        imported_by TEXT NOT NULL DEFAULT 'system',
        status TEXT NOT NULL DEFAULT 'active',
        checksum TEXT NOT NULL,
        notes TEXT,
        previous_risk_rating TEXT,
        previous_fatf_status TEXT,
        UNIQUE(snapshot_id, country_key)
    );

    CREATE INDEX IF NOT EXISTS idx_country_risk_entries_lookup
        ON country_risk_entries(snapshot_id, country_key, status);

    CREATE INDEX IF NOT EXISTS idx_country_risk_entries_fatf
        ON country_risk_entries(fatf_status, status);
    """)
    logger.info("PR-CR1R: country-risk snapshot tables ensured as dormant reference schema")


def _ensure_verification_jobs_schema(db: DBConnection) -> None:
    """Create the PR6 async verification jobs table and claim indexes."""
    if db.is_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS verification_jobs (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
                priority INTEGER NOT NULL DEFAULT 100,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                run_after TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                locked_by TEXT,
                locked_at TIMESTAMP,
                last_error TEXT,
                job_metadata JSONB DEFAULT '{}',
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS verification_jobs (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
                priority INTEGER NOT NULL DEFAULT 100,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                run_after TEXT DEFAULT (datetime('now')),
                locked_by TEXT,
                locked_at TEXT,
                last_error TEXT,
                job_metadata TEXT DEFAULT '{}',
                created_by TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_verification_jobs_status_run_after
        ON verification_jobs(status, run_after, priority, created_at)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_verification_jobs_document
        ON verification_jobs(document_id, created_at)
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_verification_jobs_active_document
        ON verification_jobs(document_id)
        WHERE status IN ('pending','retrying','in_progress')
    """)


def _ensure_screening_jobs_schema(db: DBConnection) -> None:
    """Create the async application-screening jobs table and claim indexes."""
    if db.is_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS screening_jobs (
                id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                submit_attempt_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'complyadvantage',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
                priority INTEGER NOT NULL DEFAULT 100,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                run_after TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                locked_by TEXT,
                locked_at TIMESTAMP,
                last_error TEXT,
                job_metadata JSONB DEFAULT '{}',
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS screening_jobs (
                id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                submit_attempt_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'complyadvantage',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','retrying','succeeded','failed','cancelled')),
                priority INTEGER NOT NULL DEFAULT 100,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                run_after TEXT DEFAULT (datetime('now')),
                locked_by TEXT,
                locked_at TEXT,
                last_error TEXT,
                job_metadata TEXT DEFAULT '{}',
                created_by TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_screening_jobs_status_run_after
        ON screening_jobs(status, run_after, priority, created_at)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_screening_jobs_application
        ON screening_jobs(application_id, created_at)
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_jobs_active_application
        ON screening_jobs(application_id)
        WHERE status IN ('pending','retrying','in_progress')
    """)


def _ensure_periodic_review_phase1_schema(db: DBConnection):
    """Add Phase 1 canonical periodic-review fields and evidence-link storage."""
    if not _safe_table_exists(db, "periodic_reviews"):
        return

    ts_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    bool_default = "FALSE" if db.is_postgres else "0"
    review_columns = {
        "last_review_date": "TEXT",
        "next_review_date": "TEXT",
        "assigned_officer": "TEXT REFERENCES users(id)",
        "assigned_by": "TEXT REFERENCES users(id)",
        "reassigned_reason": "TEXT",
        "review_cycle_number": "INTEGER DEFAULT 1",
        "review_type": "TEXT",
        "policy_version": "TEXT",
        "frequency_months": "INTEGER",
        "calculation_basis": "TEXT",
        "legacy_import": f"{'BOOLEAN' if db.is_postgres else 'INTEGER'} DEFAULT {bool_default}",
        "legacy_source_type": "TEXT",
        "legacy_source_note": "TEXT",
        "legacy_review_evidence_note": "TEXT",
        "legacy_confidence": "TEXT",
        "legacy_entered_by": "TEXT REFERENCES users(id)",
        "legacy_entered_at": ts_type,
        "legacy_sco_acknowledged_by": "TEXT REFERENCES users(id)",
        "legacy_sco_acknowledged_at": ts_type,
        "import_requires_ack": f"{'BOOLEAN' if db.is_postgres else 'INTEGER'} DEFAULT {bool_default}",
        "material_change_attestation": "TEXT",
        "material_change_categories": "JSONB DEFAULT '[]'" if db.is_postgres else "TEXT DEFAULT '[]'",
        "risk_change_attestation": "TEXT",
        "risk_rerate_reason": "TEXT",
        "risk_rerated_by": "TEXT REFERENCES users(id)",
        "risk_rerated_at": ts_type,
        "officer_rationale": "TEXT",
        "memo_status": "TEXT",
        "periodic_review_memo_id": "INTEGER",
    }
    for column, definition in review_columns.items():
        if not _safe_column_exists(db, "periodic_reviews", column):
            db.execute(f"ALTER TABLE periodic_reviews ADD COLUMN {column} {definition}")

    if not _safe_table_exists(db, "periodic_review_evidence_links"):
        if db.is_postgres:
            db.execute(
                """
                CREATE TABLE periodic_review_evidence_links (
                    id BIGSERIAL PRIMARY KEY,
                    periodic_review_id INTEGER NOT NULL REFERENCES periodic_reviews(id) ON DELETE CASCADE,
                    requirement_id TEXT,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    link_type TEXT,
                    linked_by TEXT REFERENCES users(id),
                    linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    note TEXT
                )
                """
            )
        else:
            db.execute(
                """
                CREATE TABLE periodic_review_evidence_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    periodic_review_id INTEGER NOT NULL REFERENCES periodic_reviews(id) ON DELETE CASCADE,
                    requirement_id TEXT,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    link_type TEXT,
                    linked_by TEXT REFERENCES users(id),
                    linked_at TEXT DEFAULT (datetime('now')),
                    note TEXT
                )
                """
            )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_prev_links_review "
        "ON periodic_review_evidence_links(periodic_review_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_prev_links_document "
        "ON periodic_review_evidence_links(document_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_assigned_officer "
        "ON periodic_reviews(assigned_officer)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_next_review_date "
        "ON periodic_reviews(next_review_date)"
    )


def _ensure_periodic_review_attestation_schema(db: DBConnection):
    """Add PRS-2 client periodic-review attestation fields."""
    if not _safe_table_exists(db, "periodic_reviews"):
        return

    ts_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    review_columns = {
        "client_attestation_status": "TEXT DEFAULT 'not_started'",
        "client_attestation_payload": "JSONB DEFAULT '{}'" if db.is_postgres else "TEXT DEFAULT '{}'",
        "client_attestation_saved_at": ts_type,
        "client_attestation_submitted_at": ts_type,
        "client_attestation_submitted_by": "TEXT REFERENCES clients(id)",
        "client_attestation_questionnaire_version": "TEXT",
    }
    for column, definition in review_columns.items():
        if not _safe_column_exists(db, "periodic_reviews", column):
            db.execute(f"ALTER TABLE periodic_reviews ADD COLUMN {column} {definition}")

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_client_attestation_status "
        "ON periodic_reviews(client_attestation_status)"
    )


def _ensure_periodic_review_baseline_schema(db: DBConnection):
    """Add PRS-2B baseline metadata fields for officer-managed setup."""
    if _safe_table_exists(db, "periodic_reviews"):
        review_columns = {
            "baseline_status": "TEXT",
            "baseline_date": "TEXT",
            "baseline_cadence_months": "INTEGER",
            "baseline_note": "TEXT",
        }
        for column, definition in review_columns.items():
            if not _safe_column_exists(db, "periodic_reviews", column):
                db.execute(f"ALTER TABLE periodic_reviews ADD COLUMN {column} {definition}")

    if not _safe_table_exists(db, "applications"):
        return

    application_columns = {
        "periodic_review_baseline_status": "TEXT",
        "periodic_review_baseline_date": "TEXT",
        "periodic_review_baseline_cadence_months": "INTEGER",
        "periodic_review_baseline_note": "TEXT",
        "periodic_review_last_review_date": "TEXT",
        "periodic_review_next_review_due": "TEXT",
        "periodic_review_baseline_calculation_basis": "TEXT",
        "periodic_review_baseline_policy_version": "TEXT",
    }
    for column, definition in application_columns.items():
        if not _safe_column_exists(db, "applications", column):
            db.execute(f"ALTER TABLE applications ADD COLUMN {column} {definition}")


def _ensure_periodic_review_findings_schema(db: DBConnection):
    """Add PRS-4 officer findings draft fields."""
    if not _safe_table_exists(db, "periodic_reviews"):
        return

    ts_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    review_columns = {
        "officer_findings_note": "TEXT",
        "officer_deficiencies_note": "TEXT",
        "officer_internal_review_note": "TEXT",
        "findings_updated_by": "TEXT REFERENCES users(id)",
        "findings_updated_at": ts_type,
    }
    for column, definition in review_columns.items():
        if not _safe_column_exists(db, "periodic_reviews", column):
            db.execute(f"ALTER TABLE periodic_reviews ADD COLUMN {column} {definition}")


def _ensure_periodic_review_notification_schema(db: DBConnection):
    """Add PRS-6 notification/reminder metadata to the canonical review shell."""
    if not _safe_table_exists(db, "periodic_reviews"):
        return

    ts_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    review_columns = {
        "client_notification_status": "TEXT DEFAULT 'not_sent'",
        "initial_notification_sent_at": ts_type,
        "last_reminder_sent_at": ts_type,
        "reminder_count": "INTEGER DEFAULT 0",
        "last_notification_error": "TEXT",
        "officer_alert_status": "TEXT",
        "officer_alerted_at": ts_type,
        "notification_channel": "TEXT DEFAULT 'portal'",
        "next_reminder_due_at": ts_type,
    }
    for column, definition in review_columns.items():
        if not _safe_column_exists(db, "periodic_reviews", column):
            db.execute(f"ALTER TABLE periodic_reviews ADD COLUMN {column} {definition}")

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_client_notification_status "
        "ON periodic_reviews(client_notification_status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_next_reminder_due_at "
        "ON periodic_reviews(next_reminder_due_at)"
    )


def _ensure_periodic_review_risk_reassessment_schema(db: DBConnection):
    """Add PRS-7 risk reassessment and memo addendum metadata."""
    if not _safe_table_exists(db, "periodic_reviews"):
        return

    ts_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    bool_default = "FALSE" if db.is_postgres else "0"
    review_columns = {
        "risk_reassessment_status": "TEXT DEFAULT 'not_started'",
        "risk_impact_category": "TEXT",
        "officer_risk_decision": "TEXT",
        "confirmed_risk_level": (
            "TEXT CHECK(confirmed_risk_level IS NULL OR "
            "confirmed_risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH'))"
        ),
        "risk_reassessment_rationale": "TEXT",
        "risk_reassessment_saved_by": "TEXT REFERENCES users(id)",
        "risk_reassessment_saved_at": ts_type,
        "senior_review_required": f"{'BOOLEAN' if db.is_postgres else 'INTEGER'} DEFAULT {bool_default}",
        "senior_review_reason": "TEXT",
        "memo_addendum_status": "TEXT DEFAULT 'not_generated'",
        "memo_addendum_generated_at": ts_type,
        "memo_addendum_finalized_at": ts_type,
        "memo_addendum_finalized_by": "TEXT REFERENCES users(id)",
    }
    for column, definition in review_columns.items():
        if not _safe_column_exists(db, "periodic_reviews", column):
            db.execute(f"ALTER TABLE periodic_reviews ADD COLUMN {column} {definition}")

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_risk_reassessment_status "
        "ON periodic_reviews(risk_reassessment_status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_memo_addendum_status "
        "ON periodic_reviews(memo_addendum_status)"
    )


SUPERVISOR_AUDIT_LOG_COLUMNS = (
    "id",
    "timestamp",
    "event_type",
    "severity",
    "pipeline_id",
    "application_id",
    "run_id",
    "agent_type",
    "actor_type",
    "actor_id",
    "actor_name",
    "actor_role",
    "action",
    "detail",
    "data_json",
    "ip_address",
    "session_id",
    "previous_hash",
    "entry_hash",
)


def _table_column_info(db: DBConnection, table: str) -> Dict[str, Dict[str, str]]:
    """Return lowercase column metadata for SQLite or PostgreSQL."""
    if db.is_postgres:
        rows = db.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = ?
            """,
            (table,),
        ).fetchall()
        return {
            str(row["column_name"]).lower(): {"type": str(row["data_type"] or "")}
            for row in rows
        }

    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        str(row["name"]).lower(): {"type": str(row["type"] or "")}
        for row in rows
    }


def _supervisor_audit_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = "" if value is None else str(value).strip()
    if text:
        return text
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return {}
    return {}


def _supervisor_audit_hash_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "audit_id": row["id"],
        "timestamp": row["timestamp"],
        "event_type": row["event_type"],
        "severity": row["severity"] or "info",
        "pipeline_id": row["pipeline_id"] or "",
        "application_id": row["application_id"] or "",
        "run_id": row["run_id"] or "",
        "agent_type": row["agent_type"] or "",
        "actor_type": row["actor_type"] or "system",
        "actor_id": row["actor_id"] or "",
        "actor_name": row["actor_name"] or "",
        "actor_role": row["actor_role"] or "",
        "action": row["action"],
        "detail": row["detail"] or "",
        "data": _safe_json_object(row["data_json"]),
        "previous_hash": row["previous_hash"] or "",
        "hash_version": 2,
    }


def _compute_supervisor_audit_entry_hash(row: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_supervisor_audit_hash_payload(row), sort_keys=True).encode()
    ).hexdigest()


# ===========================================================================
# General audit_log hash chain (PR-27 / audit-log-tamper-evidence-1) — CORE.
#
# Mirrors the shipped, audited supervisor_audit_log chain (above +
# supervisor/audit.py) but for the general audit_log table. This is the
# decision-INDEPENDENT core: schema (v2.46 migration), the canonical hash, a
# single append chokepoint, and a verifier with a legacy/coverage-gap model.
# Routing the ~200 existing audit_log writers through append_audit_log is a
# SEPARATE, decision-gated step (global-lock throughput + sequencing after the
# ownership PR) and is intentionally NOT done here — legacy hash-less rows stay
# valid and are classified as `legacy` by the verifier.
# ===========================================================================

# Distinct from the supervisor chain's advisory lock (8674309921) so the two
# chains never serialise against each other.
_AUDIT_LOG_CHAIN_LOCK_KEY = 8674309922


def _audit_log_ts_for_hash(value: Any) -> str:
    """Normalize a stored timestamp to the exact string that was hashed.

    append_audit_log stores an explicit "%Y-%m-%dT%H:%M:%SZ" string. PostgreSQL
    returns it as a datetime on read; SQLite returns the string. Normalizing both
    back to the same string keeps append-time and verify-time hashes identical
    across engines (mirrors supervisor/audit.py _timestamp_for_hash)."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return "" if value is None else str(value)


def _audit_log_state_str(value: Any) -> Optional[str]:
    """Serialize before/after_state ONCE to the string that is both stored and
    hashed (so stored bytes == hashed bytes). dicts -> deterministic JSON;
    strings pass through; None -> None (stored NULL, hashed as "")."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _audit_log_hash_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical audit_log chain payload (hash_version=1).

    Deliberately EXCLUDES the DB serial id (unknown at insert time, unlike the
    supervisor chain's app-generated TEXT id) — ordering integrity comes from the
    previous_hash links, not the serial. Null optionals hash as "" so a NULL and
    an empty string are indistinguishable and can't be used to forge a match."""
    return {
        "user_id": row.get("user_id") or "",
        "user_name": row.get("user_name") or "",
        "user_role": row.get("user_role") or "",
        "action": row.get("action") or "",
        "target": row.get("target") or "",
        "detail": row.get("detail") or "",
        "ip_address": row.get("ip_address") or "",
        "before_state": row.get("before_state") or "",
        "after_state": row.get("after_state") or "",
        "timestamp": _audit_log_ts_for_hash(row.get("timestamp")),
        "previous_hash": row.get("previous_hash") or "",
        "hash_version": 1,
    }


def _compute_audit_log_entry_hash(row: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_audit_log_hash_payload(row), sort_keys=True).encode()
    ).hexdigest()


def append_audit_log(
    db: 'DBConnection',
    *,
    action: str,
    user_id: str = "",
    user_name: str = "",
    user_role: str = "",
    target: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
    before_state: Any = None,
    after_state: Any = None,
    application_id: Optional[str] = None,
    request_id: Optional[str] = None,
    commit: bool = False,
) -> str:
    """Append a hash-chained entry to audit_log and return its entry_hash.

    Operates on the caller's OPEN connection/transaction so the audit row commits
    atomically with the caller's work; does not commit unless commit=True. On
    failure the exception propagates (fail-closed — the caller's transaction is
    never committed with a missing audit row).

    Concurrency: a PostgreSQL transaction-scoped advisory lock makes the
    tail-select + insert atomic across sessions so two appends cannot chain off
    the same tail (a fork). SQLite already serialises writers; the partial unique
    index on previous_hash (v2.46) is the structural backstop on both engines.

    CONTRACT — the advisory lock is a SINGLE global key held until the caller's
    transaction commits, so it SERIALISES every audit append across all workers.
    A caller MUST commit promptly after append_audit_log and MUST NOT interleave
    slow work (external I/O, long computation) between append and commit, or it
    stalls every other audit writer (and can abort at statement_timeout, failing
    the action fail-closed). Before this is wired into the ~200 existing writers,
    that throughput trade-off must be decided (commit-immediately here vs a
    short dedicated audit transaction) — hence wiring is deliberately deferred.

    Keyword-only with None/"" defaults so it absorbs every existing audit_log
    caller's argument subset when the wiring step (deferred) routes them here.
    application_id is write-through metadata only: callers with reliable app
    context must pass it in; this function deliberately performs no target/ref
    lookup and opens no second DB connection while appending the chain row.
    """
    if request_id is None:
        try:
            from observability import get_request_id as _get_request_id
            request_id = _get_request_id()
        except Exception:
            request_id = None

    scoped_application_id = (
        str(application_id).strip()[:128]
        if application_id is not None and str(application_id).strip()
        else None
    )

    if getattr(db, "is_postgres", False):
        db.execute("SELECT pg_advisory_xact_lock(?)", (_AUDIT_LOG_CHAIN_LOCK_KEY,))

    # Tail = the chained entry whose hash no other entry references as its
    # predecessor. entry_hash IS NOT NULL skips legacy (pre-chain) rows so the
    # chain starts cleanly at the first appended entry. id DESC is unambiguous
    # (monotonic PK), unlike a second-granularity timestamp tie-break.
    tail = db.execute(
        """
        SELECT a.entry_hash AS entry_hash
          FROM audit_log a
         WHERE a.entry_hash IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM audit_log b WHERE b.previous_hash = a.entry_hash
           )
         ORDER BY a.id DESC
         LIMIT 1
        """
    ).fetchone()
    previous_hash = (tail["entry_hash"] if tail else None)

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    before_str = _audit_log_state_str(before_state)
    after_str = _audit_log_state_str(after_state)
    row = {
        "user_id": user_id or "",
        "user_name": user_name or "",
        "user_role": user_role or "",
        "action": action,
        "target": target,
        "detail": detail,
        "ip_address": ip_address,
        "before_state": before_str,
        "after_state": after_str,
        "timestamp": timestamp,  # supplied explicitly so stored == hashed
        "previous_hash": previous_hash,
    }
    entry_hash = _compute_audit_log_entry_hash(row)

    db.execute(
        """
        INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address,
             timestamp, before_state, after_state, previous_hash, entry_hash,
             application_id, request_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id or None, user_name or None, user_role or None, action,
            target, detail, ip_address, timestamp, before_str, after_str,
            previous_hash, entry_hash, scoped_application_id, request_id or None,
        ),
    )
    if commit:
        db.commit()
    return entry_hash


def verify_audit_log_chain(db: 'DBConnection', limit: Optional[int] = None) -> Dict[str, Any]:
    """Verify the audit_log hash chain.

    `verified` reflects INTEGRITY ONLY — content tampering, broken links, forks,
    orphans, duplicates, cycles. Whether every post-genesis row is chained
    (COVERAGE) is reported SEPARATELY via `coverage_complete` / `coverage_gaps`
    and does NOT flip `verified`. This matters during the (deferred) wiring
    rollout: until every writer is migrated, hash-less rows after the genesis are
    EXPECTED; if those forced `verified: False`, operators would learn to ignore
    the flag and a genuine tamper would be buried under expected gaps. So a real
    tamper is still reported `verified: False` even while coverage is partial.

    Order is reconstructed by FOLLOWING previous_hash -> entry_hash links (not by
    timestamp, which is second-granularity and non-deterministic on PostgreSQL).

    Coverage model (full scan only — limit=None):
      - genesis_id = MIN(id) among chained rows (entry_hash IS NOT NULL).
      - unchained rows with id < genesis_id  -> `legacy` (ALLOWED: pre-chain).
      - unchained rows with id > genesis_id  -> `coverage_gap` (a raw INSERT
        bypassed append_audit_log after the chain started) -> reported; does NOT
        fail integrity.

    `limit`: when a positive int, only the most recent `limit` CHAINED rows are
    fetched and integrity-checked — a BOUNDED recency check that does not scan
    the whole table. Coverage classification is skipped in that mode
    (`coverage_complete` is None). With limit=None the whole table is scanned
    (O(N) — this is an operator/audit tool, not a hot path).

    Retention tolerance: the chain head is the earliest SURVIVING chained row
    whose previous_hash is not the entry_hash of any surviving chained row, so a
    GDPR retention delete of the oldest (contiguous) entries does not false-fail.

    INTEGRITY LIMITATION — read before trusting `verified: True`. This is an
    UNKEYED SHA-256 chain over a PUBLIC payload with NO external anchor. It proves
    the rows are internally CONSISTENT; it does NOT prove they are AUTHENTIC
    against an adversary holding UPDATE/DELETE on audit_log. Such an insider can
    edit any entry and cascade-recompute previous_hash+entry_hash for every later
    row, and can drop the newest N entries (suffix truncation) — both leave a
    chain that verifies True. Defeating the write-capable insider needs a keyed
    MAC (HMAC key in Secrets Manager, unreachable via SQL) plus periodic
    out-of-band sealing of the tail hash+count to WORM storage — a tracked
    follow-up. `verified: True` here means "internally consistent", not
    "tamper-proof against a database-capable actor".
    """
    windowed = isinstance(limit, int) and limit > 0
    _cols = ("SELECT id, user_id, user_name, user_role, action, target, detail, "
             "ip_address, timestamp, before_state, after_state, previous_hash, entry_hash "
             "FROM audit_log")
    try:
        if windowed:
            # Bounded fetch: only the most recent `limit` CHAINED rows (integrity
            # recency check; does not scan the whole table).
            rows = [dict(r) for r in db.execute(
                _cols + " WHERE entry_hash IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()]
            rows.reverse()  # ascending
            chained = rows
            unchained: List[Dict[str, Any]] = []
        else:
            rows = [dict(r) for r in db.execute(_cols + " ORDER BY id ASC").fetchall()]
            chained = [r for r in rows if r.get("entry_hash")]
            unchained = [r for r in rows if not r.get("entry_hash")]
    except Exception as e:  # columns absent (migration not run) etc.
        return {"verified": False, "reason": str(e)}

    if not chained:
        # Nothing chained yet: a fully-legacy table is vacuously consistent.
        return {
            "verified": True,
            "status": "no_chained_entries",
            "entries_checked": 0,
            "chained_rows": 0,
            "legacy_rows": len(unchained),
            "coverage_gaps": 0,
            "coverage_complete": None if windowed else True,
            "broken_links": [],
            "total_entries": len(rows),
        }

    broken_links: List[Dict[str, Any]] = []   # INTEGRITY breaks — these flip verified
    legacy_rows: List[Dict[str, Any]] = []
    coverage_gaps: List[Dict[str, Any]] = []  # informational — do NOT flip verified

    if not windowed:
        genesis_id = min(r["id"] for r in chained)
        legacy_rows = [r for r in unchained if r["id"] < genesis_id]
        coverage_gaps = [r for r in unchained if r["id"] > genesis_id]

    # Duplicate entry hashes are impossible in a valid chain.
    seen_hashes: Dict[str, Any] = {}
    for r in chained:
        h = r.get("entry_hash")
        if h in seen_hashes:
            broken_links.append({"entry_id": r.get("id"), "issue": "duplicate_entry_hash"})
        seen_hashes[h] = r

    chained_hashes = set(seen_hashes.keys())
    successors: Dict[str, List[Dict[str, Any]]] = {}
    heads: List[Dict[str, Any]] = []
    for r in chained:
        ph = r.get("previous_hash") or None
        # A head is any chained row whose predecessor is not itself a surviving
        # chained row: true genesis (ph is None), post-retention earliest row
        # (ph points at a deleted predecessor), OR — in windowed mode — the
        # earliest in-window row (ph points at an older row outside the window).
        if ph is None or ph not in chained_hashes:
            heads.append(r)
        if ph is not None:
            successors.setdefault(ph, []).append(r)

    ordered: List[Dict[str, Any]] = []
    if len(heads) != 1:
        broken_links.append({
            "issue": "head_count",
            "detail": f"expected exactly one chain head, found {len(heads)}",
        })

    if len(heads) == 1:
        seen = set()
        current = heads[0]
        while current is not None:
            ch = current.get("entry_hash")
            if ch in seen:
                broken_links.append({"entry_id": current.get("id"), "issue": "cycle_detected"})
                break
            seen.add(ch)
            ordered.append(current)
            nxts = successors.get(ch, [])
            if len(nxts) > 1:
                broken_links.append({
                    "issue": "chain_fork",
                    "after_entry_id": current.get("id"),
                    "successor_count": len(nxts),
                })
            current = nxts[0] if nxts else None
        for r in chained:
            if r.get("entry_hash") not in seen:
                broken_links.append({"entry_id": r.get("id"), "issue": "orphan_entry"})
    else:
        ordered = sorted(chained, key=lambda r: r["id"])

    for idx, row in enumerate(ordered):
        expected = _compute_audit_log_entry_hash(row)
        if row.get("entry_hash") != expected:
            broken_links.append({
                "entry_id": row.get("id"),
                "issue": "content_tampered",
                "expected_hash": expected,
                "actual_hash": row.get("entry_hash"),
            })
        # Link check: every entry except the head must chain off its predecessor.
        if idx > 0:
            prev = ordered[idx - 1]
            if (row.get("previous_hash") or None) != (prev.get("entry_hash") or None):
                broken_links.append({
                    "entry_id": row.get("id"),
                    "issue": "previous_hash_mismatch",
                    "expected_previous": prev.get("entry_hash"),
                    "actual_previous": row.get("previous_hash"),
                })

    return {
        "verified": len(broken_links) == 0,   # INTEGRITY only — coverage is separate
        "entries_checked": len(ordered),
        "chained_rows": len(chained),
        "legacy_rows": len(legacy_rows),
        "coverage_gaps": len(coverage_gaps),
        "coverage_complete": None if windowed else (len(coverage_gaps) == 0),
        "broken_links": broken_links,
        "total_entries": len(rows),
    }


def _create_supervisor_audit_log_table(db: DBConnection, table_name: str = "supervisor_audit_log") -> None:
    timestamp_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    timestamp_default = "CURRENT_TIMESTAMP" if db.is_postgres else "(datetime('now'))"
    db.executescript(f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id TEXT PRIMARY KEY,
        timestamp {timestamp_type} NOT NULL DEFAULT {timestamp_default},
        event_type TEXT NOT NULL,
        severity TEXT DEFAULT 'info',
        pipeline_id TEXT,
        application_id TEXT,
        run_id TEXT,
        agent_type TEXT,
        actor_type TEXT,
        actor_id TEXT,
        actor_name TEXT,
        actor_role TEXT,
        action TEXT NOT NULL,
        detail TEXT,
        data_json TEXT DEFAULT '{{}}',
        ip_address TEXT,
        session_id TEXT,
        previous_hash TEXT,
        entry_hash TEXT
    );
    """)


def _create_supervisor_audit_log_indexes(db: DBConnection) -> None:
    db.executescript("""
    CREATE INDEX IF NOT EXISTS idx_sup_audit_ts ON supervisor_audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_event ON supervisor_audit_log(event_type);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_app ON supervisor_audit_log(application_id);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_pipeline ON supervisor_audit_log(pipeline_id);
    CREATE INDEX IF NOT EXISTS idx_sup_audit_actor ON supervisor_audit_log(actor_id);
    """)
    # Structural anti-fork backstop (audit finding H12 / B3): at most one entry
    # may reference a given predecessor hash, so two concurrent appends can never
    # both chain off the same tail. Genesis entries carry NULL previous_hash and
    # are excluded by the partial predicate (valid on both PostgreSQL and SQLite
    # >= 3.8). Best-effort: if a pre-existing fork already violates uniqueness we
    # log loudly rather than block startup — the fork is then visible for repair
    # and new forks are still prevented once the index exists.
    try:
        db.executescript(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_sup_audit_prev_hash "
            "ON supervisor_audit_log(previous_hash) WHERE previous_hash IS NOT NULL;"
        )
    except Exception as exc:  # pre-existing duplicate previous_hash (a legacy fork)
        logger.error(
            "Could not create unique anti-fork index on supervisor_audit_log.previous_hash "
            "(a pre-existing chain fork likely exists and needs investigation): %s",
            exc,
        )


def _create_supervisor_audit_migrations_table(db: DBConnection) -> None:
    """Immutable record of any supervisor-audit schema migration (audit finding B2).

    A legacy -> modern schema migration necessarily rehashes rows into the new
    column layout. If the original table were simply dropped, that rebuild would
    silently re-seal any pre-migration tampering into a clean-looking chain. To
    keep the migration non-silent and pre-migration tampering detectable we
    (a) preserve the original table as an archive and (b) record a seal over the
    ordered legacy (id, entry_hash) sequence here. The seal plus the archived
    table let an auditor prove the rebuilt chain corresponds to the original,
    unaltered legacy rows.
    """
    timestamp_type = "TIMESTAMP" if db.is_postgres else "TEXT"
    timestamp_default = "CURRENT_TIMESTAMP" if db.is_postgres else "(datetime('now'))"
    db.executescript(f"""
    CREATE TABLE IF NOT EXISTS supervisor_audit_migrations (
        id TEXT PRIMARY KEY,
        migrated_at {timestamp_type} NOT NULL DEFAULT {timestamp_default},
        legacy_row_count INTEGER NOT NULL,
        legacy_chain_seal TEXT NOT NULL,
        modern_chain_head TEXT,
        archive_table TEXT NOT NULL,
        note TEXT
    );
    """)


def _modern_supervisor_audit_row(raw: Dict[str, Any], previous_hash: Optional[str]) -> Dict[str, Any]:
    detail = raw.get("detail")
    if detail in (None, ""):
        detail = raw.get("details") or ""

    data = _safe_json_object(raw.get("data_json"))
    if not data:
        data = _safe_json_object(raw.get("details"))
    legacy_hash = raw.get("entry_hash")
    legacy_previous = raw.get("previous_hash") or raw.get("prev_hash")
    if legacy_hash:
        data.setdefault("_legacy_entry_hash", legacy_hash)
    if legacy_previous:
        data.setdefault("_legacy_previous_hash", legacy_previous)

    actor = raw.get("actor")
    actor_id = raw.get("actor_id") or actor or ""

    row = {
        "id": str(raw.get("id") or secrets.token_hex(8)),
        "timestamp": _supervisor_audit_timestamp(raw.get("timestamp")),
        "event_type": raw.get("event_type") or "system_error",
        "severity": raw.get("severity") or "info",
        "pipeline_id": raw.get("pipeline_id") or None,
        "application_id": raw.get("application_id") or None,
        "run_id": raw.get("run_id") or None,
        "agent_type": raw.get("agent_type") or None,
        "actor_type": raw.get("actor_type") or ("system" if not actor else "agent"),
        "actor_id": actor_id or None,
        "actor_name": raw.get("actor_name") or None,
        "actor_role": raw.get("actor_role") or None,
        "action": raw.get("action") or str(raw.get("event_type") or "Supervisor audit event"),
        "detail": detail,
        "data_json": json.dumps(data, default=str, sort_keys=True),
        "ip_address": raw.get("ip_address") or None,
        "session_id": raw.get("session_id") or None,
        "previous_hash": previous_hash,
        "entry_hash": None,
    }
    row["entry_hash"] = _compute_supervisor_audit_entry_hash(row)
    return row


def _insert_supervisor_audit_row(db: DBConnection, table_name: str, row: Dict[str, Any]) -> None:
    columns = ", ".join(SUPERVISOR_AUDIT_LOG_COLUMNS)
    placeholders = ", ".join("?" for _ in SUPERVISOR_AUDIT_LOG_COLUMNS)
    db.execute(
        f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})",
        tuple(row.get(column) for column in SUPERVISOR_AUDIT_LOG_COLUMNS),
    )


def _supervisor_audit_log_schema_is_current(db: DBConnection) -> bool:
    if not _safe_table_exists(db, "supervisor_audit_log"):
        return False
    columns = _table_column_info(db, "supervisor_audit_log")
    if any(column not in columns for column in SUPERVISOR_AUDIT_LOG_COLUMNS):
        return False
    id_type = columns.get("id", {}).get("type", "").lower()
    return "text" in id_type or "char" in id_type


def _ensure_supervisor_audit_log_schema(db: DBConnection) -> None:
    """Repair legacy supervisor audit tables to the current hash-chain schema.

    Early migration_002 builds created ``supervisor_audit_log`` with an
    integer id and legacy column names (``details`` / ``prev_hash``) and no
    ``severity``. Current supervisor verdict writes are fail-closed and need
    the modern schema, so repair the table before any request can run.
    """
    if _supervisor_audit_log_schema_is_current(db):
        _create_supervisor_audit_log_indexes(db)
        db.execute(
            "UPDATE supervisor_audit_log SET severity = 'info' "
            "WHERE severity IS NULL OR TRIM(severity) = ''"
        )
        return

    legacy_rows = []
    if _safe_table_exists(db, "supervisor_audit_log"):
        legacy_rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM supervisor_audit_log ORDER BY timestamp ASC"
            ).fetchall()
        ]
        if not legacy_rows:
            # Empty legacy table — nothing to preserve or seal. Replace it in
            # place with the modern schema rather than creating an empty archive.
            db.execute("DROP TABLE supervisor_audit_log")
            _create_supervisor_audit_log_table(db)
            _create_supervisor_audit_log_indexes(db)
            logger.info("Supervisor audit schema replaced (empty legacy table)")
            return
        db.execute("DROP TABLE IF EXISTS supervisor_audit_log_repair")
        _create_supervisor_audit_log_table(db, "supervisor_audit_log_repair")
        previous_hash = None
        for raw in legacy_rows:
            repaired = _modern_supervisor_audit_row(raw, previous_hash)
            _insert_supervisor_audit_row(db, "supervisor_audit_log_repair", repaired)
            previous_hash = repaired["entry_hash"]

        # audit finding B2: DO NOT destroy the original chain. Rehashing rows into
        # the modern schema yields a self-consistent chain that would hide any
        # pre-migration tampering if the source were dropped. Preserve the original
        # table as an immutable archive and seal the legacy hash sequence so the
        # rebuilt chain remains provably derived from the untouched source.
        archive_suffix = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(3)
        archive_table = f"supervisor_audit_log_legacy_{archive_suffix}"
        legacy_seal = hashlib.sha256(
            "\n".join(
                f"{raw.get('id')}:{raw.get('entry_hash') or ''}" for raw in legacy_rows
            ).encode()
        ).hexdigest()

        db.execute(f"ALTER TABLE supervisor_audit_log RENAME TO {archive_table}")
        db.execute("ALTER TABLE supervisor_audit_log_repair RENAME TO supervisor_audit_log")

        _create_supervisor_audit_migrations_table(db)
        db.execute(
            "INSERT INTO supervisor_audit_migrations "
            "(id, legacy_row_count, legacy_chain_seal, modern_chain_head, archive_table, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                secrets.token_hex(8),
                len(legacy_rows),
                legacy_seal,
                previous_hash,
                archive_table,
                "Legacy supervisor_audit_log rehashed to modern schema; "
                "original archived and sealed for tamper-evidence (audit finding B2).",
            ),
        )
    else:
        _create_supervisor_audit_log_table(db)

    _create_supervisor_audit_log_indexes(db)
    db.execute(
        "UPDATE supervisor_audit_log SET severity = 'info' "
        "WHERE severity IS NULL OR TRIM(severity) = ''"
    )
    if legacy_rows:
        logger.warning(
            "Supervisor audit schema migrated: %d legacy row(s) rehashed into the "
            "modern chain. Original preserved and sealed for tamper-evidence "
            "(see supervisor_audit_migrations). (audit finding B2)",
            len(legacy_rows),
        )
    else:
        logger.info("Supervisor audit schema ensured")


def _pg_quote_identifier(identifier: str) -> str:
    """Quote a PostgreSQL identifier returned from pg_catalog."""
    return '"' + str(identifier).replace('"', '""') + '"'


def _sql_literal_list(values) -> str:
    """Return a single-quoted SQL literal list for static enum values."""
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def _postgres_check_constraints_for_column(db: DBConnection, table: str, column: str):
    """Return CHECK constraints that reference a column on a PostgreSQL table.

    This deliberately uses query parameters for LIKE patterns.  Embedding
    ``%column%`` in SQL text is unsafe with psycopg2 because the DB wrapper
    sends the query through psycopg2's pyformat machinery.
    """
    if not db.is_postgres:
        return []
    if not _safe_table_exists(db, table):
        return []
    return db.execute(
        """
        SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE t.relname = ?
           AND c.contype = 'c'
           AND (
                c.conname ILIKE ?
                OR pg_get_constraintdef(c.oid) ILIKE ?
                OR EXISTS (
                    SELECT 1
                      FROM pg_attribute a
                     WHERE a.attrelid = c.conrelid
                       AND a.attnum = ANY(c.conkey)
                       AND a.attname = ?
                )
           )
         ORDER BY c.conname
        """,
        (table, f"%{column}%", f"%{column}%", column),
    ).fetchall()


def _replace_postgres_column_check_constraint(
    db: DBConnection,
    *,
    table: str,
    column: str,
    constraint_name: str,
    allowed_values,
) -> List[str]:
    """Replace CHECK constraints for a PostgreSQL enum-like text column.

    Long-lived staging databases have carried stale CHECK constraints whose
    names are not reliable.  We therefore discover constraints by table +
    constrained column, drop only those CHECK constraints, and recreate the
    canonical constraint with the current allowed values.
    """
    if not db.is_postgres or not _safe_column_exists(db, table, column):
        return []

    constraints = _postgres_check_constraints_for_column(db, table, column)
    dropped = []
    quoted_table = _pg_quote_identifier(table)
    for constraint in constraints:
        name = constraint.get("conname")
        if not name:
            continue
        db.execute(
            f"ALTER TABLE {quoted_table} DROP CONSTRAINT IF EXISTS {_pg_quote_identifier(name)}"
        )
        dropped.append(name)

    db.execute(
        f"ALTER TABLE {quoted_table} ADD CONSTRAINT {_pg_quote_identifier(constraint_name)} "
        f"CHECK ({_pg_quote_identifier(column)} IN ({_sql_literal_list(allowed_values)}))"
    )
    logger.info(
        "Repaired PostgreSQL CHECK constraint %s.%s: dropped=%s allowed=%s",
        table,
        column,
        dropped or [],
        list(allowed_values),
    )
    return dropped


def _document_row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


_DOCUMENT_PERSON_TYPE_PREFIXES = (
    ("director", ("dir", "director")),
    ("ubo", ("ubo",)),
    ("intermediary", ("int", "inter", "intermediary")),
)


def _document_normalize_person_type(value):
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in ("director", "directors", "dir"):
        return "director"
    if normalized in ("ubo", "ubos", "beneficial_owner"):
        return "ubo"
    if normalized in ("intermediary", "intermediaries", "inter", "int"):
        return "intermediary"
    return None


def _document_person_type_from_prefix(person_id):
    value = str(person_id or "").strip().lower()
    for person_type, prefixes in _DOCUMENT_PERSON_TYPE_PREFIXES:
        if any(value.startswith(prefix) for prefix in prefixes):
            return person_type
    return None


def _document_person_type_for_ref(db: DBConnection, application_id, person_id):
    person_ref = str(person_id or "").strip()
    if not person_ref:
        return None

    prefix_type = _document_person_type_from_prefix(person_ref)
    matches = []
    lookup_sql = {
        "director": "SELECT 1 FROM directors WHERE application_id = ? AND (id = ? OR person_key = ?) LIMIT 1",
        "ubo": "SELECT 1 FROM ubos WHERE application_id = ? AND (id = ? OR person_key = ?) LIMIT 1",
        "intermediary": "SELECT 1 FROM intermediaries WHERE application_id = ? AND (id = ? OR person_key = ?) LIMIT 1",
    }
    for person_type, sql in lookup_sql.items():
        table = {
            "director": "directors",
            "ubo": "ubos",
            "intermediary": "intermediaries",
        }[person_type]
        if not _safe_table_exists(db, table):
            continue
        if db.execute(sql, (application_id, person_ref, person_ref)).fetchone():
            matches.append(person_type)

    if prefix_type and (not matches or prefix_type in matches):
        return prefix_type
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "document versioning repair found ambiguous person reference; "
            "application_id=%s person_id=%s matched_types=%s using=%s",
            application_id,
            person_ref,
            matches,
            matches[0],
        )
        return matches[0]
    return prefix_type or "unknown"


def _document_default_slot_key(doc_type, person_id=None, person_type=None):
    doc_type = str(doc_type or "general").strip() or "general"
    person_id = str(person_id or "").strip()
    if person_id:
        normalized_type = _document_normalize_person_type(person_type) or "unknown"
        return f"person:{normalized_type}:{person_id}:{doc_type}"
    return f"entity:{doc_type}"


def _document_legacy_person_slot_key(doc_type, person_id=None):
    doc_type = str(doc_type or "general").strip() or "general"
    person_id = str(person_id or "").strip()
    return f"person:{person_id}:{doc_type}" if person_id else None


def _document_truthy_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in ("0", "false", "f", "no", "n", "")


def _ensure_document_versioning_columns(db: DBConnection):
    """Ensure documents has active-versioning columns used by upload slots."""
    columns = [
        ("slot_key", "TEXT"),
        ("is_current", "BOOLEAN DEFAULT TRUE" if db.is_postgres else "INTEGER DEFAULT 1"),
        ("version", "INTEGER DEFAULT 1"),
        ("superseded_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
        ("superseded_by_document_id", "TEXT REFERENCES documents(id)"),
        ("replaced_reason", "TEXT"),
        ("replaced_by_user_id", "TEXT"),
    ]
    for column, definition in columns:
        if not _safe_column_exists(db, "documents", column):
            db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_documents_current_slot ON documents(application_id, slot_key, is_current)")


def _ensure_document_health_columns(db: DBConnection):
    """Ensure documents can persist deterministic expiry metadata."""
    columns = [
        ("expiry_date", "TIMESTAMP" if db.is_postgres else "TEXT"),
        ("valid_until", "TIMESTAMP" if db.is_postgres else "TEXT"),
        ("expiry_source", "TEXT"),
        ("expiry_confidence", "REAL"),
        ("expiry_extracted_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
    ]
    for column, definition in columns:
        if not _safe_column_exists(db, "documents", column):
            db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")


def _ensure_document_file_hash_schema(db: DBConnection):
    """Ensure documents can store upload-time SHA-256 hashes for duplicate lookup."""
    if not _safe_column_exists(db, "documents", "file_sha256"):
        db.execute("ALTER TABLE documents ADD COLUMN file_sha256 TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_application_file_sha256 "
        "ON documents(application_id, file_sha256)"
    )


def _ensure_document_evidence_classification_schema(db: DBConnection):
    """Ensure documents can distinguish pilot-proof evidence from workflow-only evidence."""
    columns = [
        ("evidence_class", "TEXT"),
        ("evidence_classification_note", "TEXT"),
        ("evidence_classified_by", "TEXT REFERENCES users(id)"),
        ("evidence_classified_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
    ]
    for column, definition in columns:
        if not _safe_column_exists(db, "documents", column):
            db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")


def _ensure_document_workflow_test_acceptance_schema(db: DBConnection):
    """Ensure documents can record governed staging-only workflow-test acceptance."""
    columns = [
        ("workflow_test_accepted", "BOOLEAN DEFAULT FALSE" if db.is_postgres else "INTEGER DEFAULT 0"),
        ("workflow_test_acceptance_reason", "TEXT"),
        ("workflow_test_accepted_by", "TEXT REFERENCES users(id)"),
        ("workflow_test_accepted_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
        ("workflow_test_acceptance_environment", "TEXT"),
    ]
    for column, definition in columns:
        if not _safe_column_exists(db, "documents", column):
            db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")


def _ensure_document_upload_audit_schema(db: DBConnection):
    """Ensure documents can record who uploaded evidence for officer auditability."""
    columns = [
        ("uploaded_by", "TEXT REFERENCES users(id)"),
        ("uploaded_by_actor_type", "TEXT"),
        ("uploaded_by_actor_id", "TEXT"),
        ("uploaded_by_display", "TEXT"),
        ("upload_source", "TEXT"),
    ]
    for column, definition in columns:
        if not _safe_column_exists(db, "documents", column):
            db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")


def _ensure_document_current_slot_unique_index(db: DBConnection):
    if db.is_postgres:
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_one_current_slot "
            "ON documents(application_id, slot_key) WHERE is_current IS TRUE"
        )
    else:
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_one_current_slot "
            "ON documents(application_id, slot_key) WHERE is_current = 1"
        )


def repair_document_current_versions(db: DBConnection):
    """Backfill document slot/version state and repair duplicate active rows.

    Existing deployments allowed multiple document rows for the same logical
    upload slot. This keeps the historical rows, assigns deterministic versions,
    and leaves only the latest row current for each application/slot.
    """
    _ensure_document_versioning_columns(db)
    normalize_legacy_doc_types(db)
    try:
        db.execute("DROP INDEX IF EXISTS idx_documents_one_current_slot")
    except Exception as e:
        if db.is_postgres and _is_postgres_lock_timeout(e):
            logger.warning(
                "document versioning repair skipped because idx_documents_one_current_slot "
                "could not be locked; startup will retry on the next run: %s",
                e,
            )
            return
        raise

    try:
        if _safe_table_exists(db, "rmi_request_items"):
            db.execute("""
                UPDATE documents
                   SET slot_key = 'rmi:' || (
                       SELECT i.id FROM rmi_request_items i
                       WHERE i.document_id = documents.id
                       LIMIT 1
                   )
                 WHERE (slot_key IS NULL OR slot_key = '')
                   AND EXISTS (
                       SELECT 1 FROM rmi_request_items i
                       WHERE i.document_id = documents.id
                   )
            """)
        if _safe_table_exists(db, "application_enhanced_requirements"):
            db.execute("""
                UPDATE documents
                   SET slot_key = 'enhanced_requirement:' || (
                       SELECT r.id FROM application_enhanced_requirements r
                       WHERE r.linked_document_id = documents.id
                       LIMIT 1
                   )
                 WHERE (slot_key IS NULL OR slot_key = '')
                   AND EXISTS (
                       SELECT 1 FROM application_enhanced_requirements r
                       WHERE r.linked_document_id = documents.id
                   )
            """)
        slot_rows = db.execute("""
            SELECT id, application_id, person_id, doc_type, slot_key
              FROM documents
        """).fetchall()
        for row in slot_rows:
            doc_id = _document_row_get(row, "id")
            current_slot_key = str(_document_row_get(row, "slot_key") or "").strip()
            if current_slot_key.startswith("rmi:") or current_slot_key.startswith("enhanced_requirement:"):
                continue
            application_id = _document_row_get(row, "application_id")
            person_id = _document_row_get(row, "person_id")
            doc_type = _document_row_get(row, "doc_type")
            person_type = _document_person_type_for_ref(db, application_id, person_id) if person_id else None
            computed_slot_key = _document_default_slot_key(doc_type, person_id, person_type)
            legacy_slot_key = _document_legacy_person_slot_key(doc_type, person_id)
            if (
                not current_slot_key
                or (legacy_slot_key and current_slot_key == legacy_slot_key)
                or current_slot_key == _document_default_slot_key(doc_type, person_id, "unknown")
                or (not person_id and current_slot_key.startswith("entity:") and current_slot_key != computed_slot_key)
                or (person_id and current_slot_key.startswith("person:") and current_slot_key != computed_slot_key)
            ):
                db.execute(
                    "UPDATE documents SET slot_key = ? WHERE id = ?",
                    (computed_slot_key, doc_id),
                )
    except Exception as e:
        logger.error("document versioning slot-key backfill failed: %s", e, exc_info=True)
        db.rollback()
        raise

    rows = db.execute("""
        SELECT id, application_id, person_id, doc_type, slot_key,
               uploaded_at, verified_at, reviewed_at, is_current, version,
               superseded_at
          FROM documents
         ORDER BY application_id, slot_key, uploaded_at, id
    """).fetchall()

    groups = {}
    for row in rows:
        key = (
            _document_row_get(row, "application_id", ""),
            _document_row_get(row, "slot_key")
            or _document_default_slot_key(
                _document_row_get(row, "doc_type"),
                _document_row_get(row, "person_id"),
                _document_person_type_for_ref(
                    db,
                    _document_row_get(row, "application_id", ""),
                    _document_row_get(row, "person_id"),
                ) if _document_row_get(row, "person_id") else None,
            ),
        )
        groups.setdefault(key, []).append(row)

    repaired_groups = 0
    ambiguous_groups = 0
    for (application_id, slot_key), group in groups.items():
        ordered = sorted(
            group,
            key=lambda r: (
                str(_document_row_get(r, "uploaded_at") or ""),
                str(_document_row_get(r, "verified_at") or ""),
                str(_document_row_get(r, "reviewed_at") or ""),
                str(_document_row_get(r, "id") or ""),
            ),
        )
        current_count = sum(
            1 for r in ordered
            if _document_truthy_bool(_document_row_get(r, "is_current", True))
        )
        versions_missing = any(_document_row_get(r, "version") in (None, "") for r in ordered)
        all_rows_already_superseded = all(bool(_document_row_get(r, "superseded_at")) for r in ordered)
        needs_repair = (
            len(ordered) > 1
            and (
                current_count > 1
                or (current_count == 0 and not all_rows_already_superseded)
            )
        )
        if not needs_repair and not versions_missing:
            continue

        if not needs_repair:
            for idx, row in enumerate(ordered):
                doc_id = _document_row_get(row, "id")
                db.execute(
                    """
                    UPDATE documents
                       SET slot_key = ?,
                           version = COALESCE(version, ?)
                     WHERE id = ?
                    """,
                    (slot_key, idx + 1, doc_id),
                )
            repaired_groups += 1
            continue

        timestamp_keys = [
            (
                str(_document_row_get(r, "uploaded_at") or ""),
                str(_document_row_get(r, "verified_at") or ""),
                str(_document_row_get(r, "reviewed_at") or ""),
            )
            for r in ordered
        ]
        if len(timestamp_keys) != len(set(timestamp_keys)):
            ambiguous_groups += 1
            logger.warning(
                "document versioning repair used id tie-breaker for ambiguous slot ordering: "
                "application_id=%s slot_key=%s document_ids=%s",
                application_id,
                slot_key,
                [_document_row_get(r, "id") for r in ordered],
            )

        for idx, row in enumerate(ordered):
            doc_id = _document_row_get(row, "id")
            is_latest = idx == len(ordered) - 1
            next_doc_id = None if is_latest else _document_row_get(ordered[idx + 1], "id")
            db.execute(
                """
                UPDATE documents
                   SET slot_key = ?,
                       version = ?,
                       is_current = ?,
                       superseded_by_document_id = ?,
                       superseded_at = CASE WHEN ? THEN NULL ELSE COALESCE(superseded_at, datetime('now')) END,
                       replaced_reason = CASE WHEN ? THEN replaced_reason ELSE COALESCE(replaced_reason, 'migration_duplicate_slot_repair') END,
                       replaced_by_user_id = CASE WHEN ? THEN replaced_by_user_id ELSE COALESCE(replaced_by_user_id, 'system') END
                 WHERE id = ?
                """,
                (
                    slot_key,
                    idx + 1,
                    True if is_latest else False,
                    next_doc_id,
                    is_latest,
                    is_latest,
                    is_latest,
                    doc_id,
                ),
            )
        repaired_groups += 1

    if repaired_groups:
        logger.info(
            "document versioning repair completed: repaired_groups=%d ambiguous_groups=%d",
            repaired_groups,
            ambiguous_groups,
        )
    _ensure_document_current_slot_unique_index(db)


_PR20_BLOCKED_BACKFILL_MARKER = "pr20_backfill_compliance_memos_blocked"


def _backfill_pr20_memo_blocked(db) -> int:
    """One-time backfill of compliance_memos.blocked / block_reason from the
    persisted memo_data JSON (PR-20).

    The hard-block verdict was historically written only into
    memo_data.metadata, never into the columns the approval gate reads, so a
    pre-existing block-verdict memo stayed gate-bypassable until regenerated.
    Row-by-row in Python so a malformed memo_data can never abort the migration
    (no SQL JSON cast); marker-gated so it runs at most once per database.
    Returns the number of rows backfilled.
    """
    db.execute(
        "CREATE TABLE IF NOT EXISTS data_migration_markers ("
        "marker_key TEXT PRIMARY KEY, description TEXT, "
        "applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    if db.execute(
        "SELECT marker_key FROM data_migration_markers WHERE marker_key=?",
        (_PR20_BLOCKED_BACKFILL_MARKER,),
    ).fetchone():
        return 0
    if not (_safe_column_exists(db, "compliance_memos", "blocked")
            and _safe_column_exists(db, "compliance_memos", "memo_data")):
        return 0

    rows = db.execute(
        "SELECT id, memo_data, block_reason FROM compliance_memos "
        "WHERE blocked IS NULL OR blocked = ?",
        (False if db.is_postgres else 0,),
    ).fetchall()
    backfilled = 0
    for r in rows:
        raw = r["memo_data"] if not isinstance(r, tuple) else r[1]
        if not raw:
            continue
        try:
            meta = (json.loads(raw) or {}).get("metadata", {}) or {}
        except (ValueError, TypeError):
            continue
        if not meta.get("blocked"):
            continue
        rid = r["id"] if not isinstance(r, tuple) else r[0]
        existing_reason = r["block_reason"] if not isinstance(r, tuple) else r[2]
        db.execute(
            "UPDATE compliance_memos SET blocked = ?, block_reason = ? WHERE id = ?",
            (True if db.is_postgres else 1, existing_reason or meta.get("block_reason"), rid),
        )
        backfilled += 1

    if db.is_postgres:
        db.execute(
            "INSERT INTO data_migration_markers (marker_key, description) VALUES (?, ?) "
            "ON CONFLICT (marker_key) DO NOTHING",
            (_PR20_BLOCKED_BACKFILL_MARKER, "PR-20 backfill blocked column from memo_data.metadata"),
        )
    else:
        db.execute(
            "INSERT OR IGNORE INTO data_migration_markers (marker_key, description) VALUES (?, ?)",
            (_PR20_BLOCKED_BACKFILL_MARKER, "PR-20 backfill blocked column from memo_data.metadata"),
        )
    db.commit()
    if backfilled:
        logger.info(
            "Migration v2.31b (PR-20): backfilled blocked column for %d legacy memo(s)",
            backfilled,
        )
    return backfilled


def _ensure_agent_executions_document_index(db: DBConnection) -> bool:
    """Create and COMMIT idx_agent_executions_document_id, then verify it.

    Performance: the application-detail document-reliance gate resolves the
    latest verify_document execution per document
    (document_reliance_gate._latest_agent_execution). agent_executions ships
    with no indexes, so that lookup was a full table scan for every document
    on every application-detail open — the dominant cost when opening a case.

    This must commit in its own transaction: a later migration step that fails
    and rolls back (e.g. v2.11 risk-level CHECK constraints against off-canon
    legacy rows) would otherwise silently discard the index — which is exactly
    what happened on staging. The verify step logs at ERROR so an absent index
    can never again hide at debug level. Returns True when the index is
    confirmed present.
    """
    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_executions_document_id "
            "ON agent_executions(document_id)"
        )
        db.commit()
    except Exception as e:
        logger.error("Failed to create idx_agent_executions_document_id: %s", e)
        try:
            db.rollback()
        except Exception:
            pass

    present = False
    try:
        if db.is_postgres:
            row = db.execute(
                "SELECT to_regclass('public.idx_agent_executions_document_id') AS idx"
            ).fetchone()
            present = bool(row and row.get("idx"))
        else:
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_agent_executions_document_id'"
            ).fetchone()
            present = bool(row)
    except Exception as e:
        logger.error("Could not verify idx_agent_executions_document_id: %s", e)
    if not present:
        logger.error(
            "PERF INDEX MISSING: idx_agent_executions_document_id is absent after "
            "migrations — application-detail opens will full-scan agent_executions."
        )
    return present


def _ensure_audit_log_append_only(db: 'DBConnection'):
    """RDI-013 non-SAR (P10-7, code half): DB-level append-only enforcement
    for audit_log — UPDATE/DELETE are blocked by engine triggers, INSERT is
    untouched.

    Bypass design (the load-bearing decision): the triggers consult the
    audit_maintenance_window table — the presence of ANY row disarms them.
    - Deployed environments boot with the window EMPTY (armed).
    - The sanctioned manual retention purge of audit_log (gdpr.purge_expired_data,
      P12-1 retention_purge context) opens a transient window via
      regulated_deletion.audit_log_maintenance_window — the one legitimate
      production deletion path is preserved.
    - Test environments (ENVIRONMENT=test/testing) auto-open a standing
      window at init so the existing fixture-cleanup and item-27
      tamper-simulation suites keep working; the dedicated P10-7 tests close
      it explicitly to exercise the armed state.

    Residual (the register's ops half, deliberately out of scope here): the
    app connects as the table owner, which could DROP/DISABLE the trigger —
    the RDS grants work (separate trigger-owner role, app role without
    UPDATE/DELETE/TRUNCATE/ALTER on audit_log) closes that.
    """
    if db.is_postgres:
        db.execute(
            "CREATE TABLE IF NOT EXISTS audit_maintenance_window ("
            "id SERIAL PRIMARY KEY, reason TEXT NOT NULL, opened_by TEXT, "
            "opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        db.executescript("""
CREATE OR REPLACE FUNCTION audit_log_append_only_guard() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM audit_maintenance_window) THEN
        RAISE EXCEPTION 'audit_log is append-only (RDI-013): open a sanctioned audit maintenance window';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_log_append_only_upd ON audit_log;
CREATE TRIGGER trg_audit_log_append_only_upd BEFORE UPDATE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only_guard();
DROP TRIGGER IF EXISTS trg_audit_log_append_only_del ON audit_log;
CREATE TRIGGER trg_audit_log_append_only_del BEFORE DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only_guard();
""")
    else:
        db.execute(
            "CREATE TABLE IF NOT EXISTS audit_maintenance_window ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL, opened_by TEXT, "
            "opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        db.executescript("""
CREATE TRIGGER IF NOT EXISTS trg_audit_log_append_only_upd
BEFORE UPDATE ON audit_log
WHEN (SELECT COUNT(*) FROM audit_maintenance_window) = 0
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (RDI-013): open a sanctioned audit maintenance window');
END;
CREATE TRIGGER IF NOT EXISTS trg_audit_log_append_only_del
BEFORE DELETE ON audit_log
WHEN (SELECT COUNT(*) FROM audit_maintenance_window) = 0
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (RDI-013): open a sanctioned audit maintenance window');
END;
""")

    env = (_CFG_ENVIRONMENT or "").strip().lower()
    if env in ("test", "testing"):
        row = db.execute(
            "SELECT COUNT(*) AS c FROM audit_maintenance_window WHERE reason = ?",
            ("nonprod_test_default",),
        ).fetchone()
        if not (row and row["c"]):
            db.execute(
                "INSERT INTO audit_maintenance_window (reason, opened_by) VALUES (?, ?)",
                ("nonprod_test_default", "init_db"),
            )


def _run_migrations(db: DBConnection):
    """Run incremental schema migrations for existing databases."""
    _ensure_country_risk_governance(db)

    # Committed + verified independently so later failing migration steps
    # cannot roll it back (see helper docstring).
    _ensure_agent_executions_document_index(db)

    # Performance: index monitoring_alert_evidence by application_id.
    # Evidence-mode screening-queue hydration batch-loads evidence for the
    # applications on the returned page (server._load_monitoring_evidence_batch);
    # the table only had indexes on monitoring_alert_id and (provider,
    # case_identifier), so that lookup was a full table scan per request and
    # degraded without bound as monitoring evidence accumulated (staging
    # measured p50 21s). CREATE INDEX IF NOT EXISTS is idempotent and
    # supported on SQLite + Postgres.
    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_app "
            "ON monitoring_alert_evidence(application_id)"
        )
    except Exception as e:
        logger.debug(f"monitoring_alert_evidence application_id index may already exist: {e}")

    # SRP-2: archive table for superseded screening reports (long-lived DBs).
    # The DBConnection wrapper rewrites AUTOINCREMENT -> SERIAL on PostgreSQL,
    # so one dialect-neutral DDL serves both. Idempotent.
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS screening_report_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                application_ref TEXT,
                archived_at TEXT DEFAULT (datetime('now')),
                archived_by TEXT NOT NULL,
                reason TEXT NOT NULL,
                report_hash TEXT NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_screening_report_archive_app "
            "ON screening_report_archive(application_id)"
        )
    except Exception as e:
        logger.debug(f"screening_report_archive may already exist: {e}")

    # DCI-104: foreign-key index coverage, batch 2. These FK columns had no
    # covering index, so joins and ON DELETE CASCADE integrity scans over them
    # were full table scans that degrade as the referenced parents grow. Indexes
    # are additive (never change query results) so they are P12-1-safe on
    # regulated / change-controlled tables. Idempotent + dialect-neutral;
    # migration_050 keeps the ADR-0008 ledger entry. The high-fan-in
    # audit-actor *_by -> users FKs are deliberately excluded (write overhead,
    # no hot reverse-lookup path). Deferred: four lower-value navigational FKs
    # on change-management tables (change_requests.source_alert_id,
    # change_request_documents.item_id, entity_profile_versions.change_request_id,
    # application_enhanced_requirements.workflow_test_acceptance_document_id) —
    # those tables/columns are created by an _ensure_* schema step that runs
    # AFTER this migration on a fresh DB, so they need their index placed there.
    for _idx_name, _idx_ddl in (
        ("idx_screening_reviews_application_id",
         "CREATE INDEX IF NOT EXISTS idx_screening_reviews_application_id ON screening_reviews(application_id)"),
        ("idx_client_sessions_application_id",
         "CREATE INDEX IF NOT EXISTS idx_client_sessions_application_id ON client_sessions(application_id)"),
        ("idx_verification_jobs_application_id",
         "CREATE INDEX IF NOT EXISTS idx_verification_jobs_application_id ON verification_jobs(application_id)"),
        ("idx_sar_reports_alert_id",
         "CREATE INDEX IF NOT EXISTS idx_sar_reports_alert_id ON sar_reports(alert_id)"),
        ("idx_aer_linked_document_id",
         "CREATE INDEX IF NOT EXISTS idx_aer_linked_document_id ON application_enhanced_requirements(linked_document_id)"),
        ("idx_supervisor_human_reviews_escalation",
         "CREATE INDEX IF NOT EXISTS idx_supervisor_human_reviews_escalation ON supervisor_human_reviews(escalation_id)"),
        ("idx_data_purge_log_retention_policy",
         "CREATE INDEX IF NOT EXISTS idx_data_purge_log_retention_policy ON data_purge_log(retention_policy_id)"),
        ("idx_documents_superseded_by",
         "CREATE INDEX IF NOT EXISTS idx_documents_superseded_by ON documents(superseded_by_document_id)"),
    ):
        try:
            db.execute(_idx_ddl)
        except Exception as e:
            logger.debug(f"{_idx_name} may already exist or column/table absent: {e}")

    # RDI-013 (P10-7): audit_log append-only triggers + maintenance window.
    # Idempotent on both engines; migration_051 keeps the ADR-0008 ledger.
    _ensure_audit_log_append_only(db)

    # Check if pre_approval columns exist on applications table
    if not _safe_column_exists(db, "applications", "pre_approval_decision"):
        logger.info("Migration v2.1: Adding pre-approval columns to applications table")
        migration_cols = [
            "ALTER TABLE applications ADD COLUMN pre_approval_decision TEXT",
            "ALTER TABLE applications ADD COLUMN pre_approval_notes TEXT",
            "ALTER TABLE applications ADD COLUMN pre_approval_officer_id TEXT",
            "ALTER TABLE applications ADD COLUMN pre_approval_timestamp TEXT",
        ]
        for stmt in migration_cols:
            try:
                db.execute(stmt)
            except Exception as e:
                logger.debug(f"Migration column may already exist: {e}")
        logger.info("Migration v2.1: Pre-approval columns added")

    # Migration: Add password reset columns to clients table
    if not _safe_column_exists(db, "clients", "password_reset_token"):
        logger.info("Migration: Adding password reset columns to clients table")
        for col in ["password_reset_token TEXT", "password_reset_expires TEXT"]:
            try:
                db.execute(f"ALTER TABLE clients ADD COLUMN {col}")
            except Exception as e:
                logger.debug(f"Migration column may already exist: {e}")
        logger.info("Migration: Password reset columns added")

    # Migration v2.2: Add scoring config columns to risk_config
    if not _safe_column_exists(db, "risk_config", "country_risk_scores"):
        logger.info("Migration v2.2: Adding scoring config columns to risk_config")
        for col in ["country_risk_scores", "sector_risk_scores", "entity_type_scores"]:
            try:
                db.execute(f"ALTER TABLE risk_config ADD COLUMN {col} TEXT DEFAULT '{{}}'")
            except Exception as e:
                logger.debug(f"Migration column {col} may already exist: {e}")
        # Populate default values for existing rows
        try:
            _populate_default_scoring_config(db)
        except Exception as e:
            logger.debug(f"Could not populate default scoring config: {e}")
        logger.info("Migration v2.2: Scoring config columns added")

    # Migration v2.3: Add s3_key column to documents table
    if not _safe_column_exists(db, "documents", "s3_key"):
        logger.info("Migration v2.3: Adding s3_key column to documents table")
        try:
            db.execute("ALTER TABLE documents ADD COLUMN s3_key TEXT")
        except Exception as e:
            logger.debug(f"Migration s3_key column may already exist: {e}")
        logger.info("Migration v2.3: s3_key column added")

    # Migration v2.5: Add stable ownership columns and intermediary table
    ownership_column_checks = {
        "directors": [
            ("person_key", "TEXT"),
            ("first_name", "TEXT"),
            ("last_name", "TEXT"),
        ],
        "ubos": [
            ("person_key", "TEXT"),
            ("first_name", "TEXT"),
            ("last_name", "TEXT"),
        ],
    }
    for table_name, columns in ownership_column_checks.items():
        for column_name, column_type in columns:
            if not _safe_column_exists(db, table_name, column_name):
                logger.info("Migration v2.5: Adding %s.%s", table_name, column_name)
                try:
                    db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                except Exception as e:
                    logger.debug("Migration column %s.%s may already exist: %s", table_name, column_name, e)

    if not _safe_table_exists(db, "intermediaries"):
        logger.info("Migration v2.5: Creating intermediaries table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS intermediaries (
                id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                person_key TEXT,
                entity_name TEXT NOT NULL,
                jurisdiction TEXT,
                ownership_pct REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS intermediaries (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                person_key TEXT,
                entity_name TEXT NOT NULL,
                jurisdiction TEXT,
                ownership_pct REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """)

    # Migration v2.7: Add durable officer review fields to documents
    document_review_columns = [
        ("review_status", "TEXT DEFAULT 'pending'"),
        ("review_comment", "TEXT"),
        ("reviewed_by", "TEXT"),
        ("reviewed_at", "TEXT" if not USE_POSTGRESQL else "TIMESTAMP"),
    ]
    for column_name, column_type in document_review_columns:
        if not _safe_column_exists(db, "documents", column_name):
            logger.info("Migration v2.7: Adding documents.%s", column_name)
            try:
                db.execute(f"ALTER TABLE documents ADD COLUMN {column_name} {column_type}")
            except Exception as e:
                logger.debug("Migration documents.%s may already exist: %s", column_name, e)

    # Migration v2.4: Add compliance_resources table for back-office reference materials
    if not _safe_table_exists(db, "compliance_resources"):
        logger.info("Migration v2.4: Creating compliance_resources table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS compliance_resources (
                id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
                slug TEXT UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT DEFAULT 'internal',
                resource_type TEXT DEFAULT 'uploaded' CHECK(resource_type IN ('system','uploaded')),
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                s3_key TEXT,
                mime_type TEXT,
                file_size INTEGER,
                uploaded_by TEXT REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_compliance_resources_category ON compliance_resources(category);
            CREATE INDEX IF NOT EXISTS idx_compliance_resources_created_at ON compliance_resources(created_at);
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS compliance_resources (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
                slug TEXT UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT DEFAULT 'internal',
                resource_type TEXT DEFAULT 'uploaded' CHECK(resource_type IN ('system','uploaded')),
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                s3_key TEXT,
                mime_type TEXT,
                file_size INTEGER,
                uploaded_by TEXT REFERENCES users(id),
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_compliance_resources_category ON compliance_resources(category);
            CREATE INDEX IF NOT EXISTS idx_compliance_resources_created_at ON compliance_resources(created_at);
            """)
        logger.info("Migration v2.4: compliance_resources table ready")

    # Migration v2.5: Add system_settings table for persisted back-office settings
    if not _safe_table_exists(db, "system_settings"):
        logger.info("Migration v2.5: Creating system_settings table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                company_name TEXT NOT NULL DEFAULT 'Onboarda Ltd',
                licence_number TEXT DEFAULT 'FSC-PIS-2024-001',
                default_retention_years INTEGER NOT NULL DEFAULT 7,
                auto_approve_max_score INTEGER NOT NULL DEFAULT 40,
                edd_threshold_score INTEGER NOT NULL DEFAULT 55,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT REFERENCES users(id)
            );
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                company_name TEXT NOT NULL DEFAULT 'Onboarda Ltd',
                licence_number TEXT DEFAULT 'FSC-PIS-2024-001',
                default_retention_years INTEGER NOT NULL DEFAULT 7,
                auto_approve_max_score INTEGER NOT NULL DEFAULT 40,
                edd_threshold_score INTEGER NOT NULL DEFAULT 55,
                updated_at TEXT DEFAULT (datetime('now')),
                updated_by TEXT REFERENCES users(id)
            );
            """)
        logger.info("Migration v2.5: system_settings table ready")

    # Migration v2.21: Enhanced / EDD requirement rules settings table
    if not _safe_table_exists(db, "enhanced_requirement_rules"):
        logger.info("Migration v2.21: Creating enhanced_requirement_rules table")
        _ensure_enhanced_requirement_rules_table(db)
        logger.info("Migration v2.21: enhanced_requirement_rules table ready")
    try:
        _ensure_default_enhanced_requirement_rules(db)
    except Exception as e:
        logger.warning("Migration v2.21: enhanced requirement seed skipped: %s", e)

    # Migration v2.6: Add regulatory_documents table for regulatory intelligence workflow
    if not _safe_table_exists(db, "regulatory_documents"):
        logger.info("Migration v2.6: Creating regulatory_documents table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS regulatory_documents (
                id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
                title TEXT NOT NULL,
                regulator TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                publication_date TEXT,
                effective_date TEXT,
                file_name TEXT,
                file_path TEXT,
                s3_key TEXT,
                mime_type TEXT,
                file_size INTEGER,
                source_text TEXT,
                status TEXT DEFAULT 'uploaded' CHECK(status IN ('uploaded','analysed','review_required','analysis_failed')),
                analysis_source TEXT,
                analysis_summary JSONB DEFAULT '{}',
                audit_trail JSONB DEFAULT '[]',
                uploaded_by TEXT REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_regulatory_documents_status ON regulatory_documents(status);
            CREATE INDEX IF NOT EXISTS idx_regulatory_documents_created_at ON regulatory_documents(created_at);
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS regulatory_documents (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
                title TEXT NOT NULL,
                regulator TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                publication_date TEXT,
                effective_date TEXT,
                file_name TEXT,
                file_path TEXT,
                s3_key TEXT,
                mime_type TEXT,
                file_size INTEGER,
                source_text TEXT,
                status TEXT DEFAULT 'uploaded' CHECK(status IN ('uploaded','analysed','review_required','analysis_failed')),
                analysis_source TEXT,
                analysis_summary TEXT DEFAULT '{}',
                audit_trail TEXT DEFAULT '[]',
                uploaded_by TEXT REFERENCES users(id),
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_regulatory_documents_status ON regulatory_documents(status);
            CREATE INDEX IF NOT EXISTS idx_regulatory_documents_created_at ON regulatory_documents(created_at);
            """)
        logger.info("Migration v2.6: regulatory_documents table ready")

    # Migration v2.8: Add sumsub_applicant_mappings table for deterministic webhook linking (Finding 12)
    if not _safe_table_exists(db, "sumsub_applicant_mappings"):
        logger.info("Migration v2.8: Creating sumsub_applicant_mappings table")
        if USE_POSTGRESQL:
            db.execute("""
                CREATE TABLE IF NOT EXISTS sumsub_applicant_mappings (
                    id SERIAL PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    applicant_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    person_name TEXT DEFAULT '',
                    person_type TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(applicant_id)
                )
            """)
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS sumsub_applicant_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id TEXT NOT NULL,
                    applicant_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    person_name TEXT DEFAULT '',
                    person_type TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(applicant_id)
                )
            """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_sam_applicant ON sumsub_applicant_mappings(applicant_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sam_external ON sumsub_applicant_mappings(external_user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sam_app ON sumsub_applicant_mappings(application_id)")
        logger.info("Migration v2.8: sumsub_applicant_mappings table ready")

    # Migration v2.9: Add supervisor pipeline results and audit log tables
    if not _safe_table_exists(db, "supervisor_pipeline_results"):
        logger.info("Migration v2.9: Creating supervisor_pipeline_results table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS supervisor_pipeline_results (
                id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
                pipeline_id TEXT NOT NULL UNIQUE,
                application_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                trigger_type TEXT,
                trigger_source TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_sup_pipeline_app ON supervisor_pipeline_results(application_id);
            CREATE INDEX IF NOT EXISTS idx_sup_pipeline_status ON supervisor_pipeline_results(status);
            CREATE INDEX IF NOT EXISTS idx_sup_pipeline_completed ON supervisor_pipeline_results(completed_at);
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS supervisor_pipeline_results (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
                pipeline_id TEXT NOT NULL UNIQUE,
                application_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                trigger_type TEXT,
                trigger_source TEXT,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_sup_pipeline_app ON supervisor_pipeline_results(application_id);
            CREATE INDEX IF NOT EXISTS idx_sup_pipeline_status ON supervisor_pipeline_results(status);
            CREATE INDEX IF NOT EXISTS idx_sup_pipeline_completed ON supervisor_pipeline_results(completed_at);
            """)
        logger.info("Migration v2.9: supervisor_pipeline_results table ready")

    if not _safe_column_exists(db, "supervisor_audit_log", "entry_hash"):
        logger.info("Migration v2.9: Creating supervisor_audit_log table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS supervisor_audit_log (
                id TEXT PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT DEFAULT 'info',
                pipeline_id TEXT,
                application_id TEXT,
                run_id TEXT,
                agent_type TEXT,
                actor_type TEXT,
                actor_id TEXT,
                actor_name TEXT,
                actor_role TEXT,
                action TEXT NOT NULL,
                detail TEXT,
                data_json TEXT DEFAULT '{}',
                ip_address TEXT,
                session_id TEXT,
                previous_hash TEXT,
                entry_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sup_audit_ts ON supervisor_audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_sup_audit_event ON supervisor_audit_log(event_type);
            CREATE INDEX IF NOT EXISTS idx_sup_audit_app ON supervisor_audit_log(application_id);
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS supervisor_audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT DEFAULT 'info',
                pipeline_id TEXT,
                application_id TEXT,
                run_id TEXT,
                agent_type TEXT,
                actor_type TEXT,
                actor_id TEXT,
                actor_name TEXT,
                actor_role TEXT,
                action TEXT NOT NULL,
                detail TEXT,
                data_json TEXT DEFAULT '{}',
                ip_address TEXT,
                session_id TEXT,
                previous_hash TEXT,
                entry_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sup_audit_ts ON supervisor_audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_sup_audit_event ON supervisor_audit_log(event_type);
            CREATE INDEX IF NOT EXISTS idx_sup_audit_app ON supervisor_audit_log(application_id);
            """)
        logger.info("Migration v2.9: supervisor_audit_log table ready")

    # Migration v2.10: Add decision_records table (normalized decision audit layer)
    if not _safe_table_exists(db, "decision_records"):
        logger.info("Migration v2.10: Creating decision_records table")
        if USE_POSTGRESQL:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS decision_records (
                id TEXT PRIMARY KEY,
                application_ref TEXT NOT NULL,
                decision_type TEXT NOT NULL CHECK(decision_type IN (
                    'approve','reject','escalate_edd','request_documents','pre_approve','request_info'
                )),
                risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
                confidence_score REAL,
                source TEXT NOT NULL CHECK(source IN ('manual','supervisor','rule_engine')),
                actor_user_id TEXT,
                actor_role TEXT,
                timestamp TIMESTAMP NOT NULL,
                key_flags TEXT DEFAULT '[]',
                override_flag INTEGER DEFAULT 0,
                override_reason TEXT,
                extra_json TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_dec_rec_app ON decision_records(application_ref);
            CREATE INDEX IF NOT EXISTS idx_dec_rec_type ON decision_records(decision_type);
            CREATE INDEX IF NOT EXISTS idx_dec_rec_ts ON decision_records(timestamp);
            """)
        else:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS decision_records (
                id TEXT PRIMARY KEY,
                application_ref TEXT NOT NULL,
                decision_type TEXT NOT NULL CHECK(decision_type IN (
                    'approve','reject','escalate_edd','request_documents','pre_approve','request_info'
                )),
                risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
                confidence_score REAL,
                source TEXT NOT NULL CHECK(source IN ('manual','supervisor','rule_engine')),
                actor_user_id TEXT,
                actor_role TEXT,
                timestamp TEXT NOT NULL,
                key_flags TEXT DEFAULT '[]',
                override_flag INTEGER DEFAULT 0,
                override_reason TEXT,
                extra_json TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_dec_rec_app ON decision_records(application_ref);
            CREATE INDEX IF NOT EXISTS idx_dec_rec_type ON decision_records(decision_type);
            CREATE INDEX IF NOT EXISTS idx_dec_rec_ts ON decision_records(timestamp);
            """)
        logger.info("Migration v2.10: decision_records table ready")

    # Migration v2.11: Add CHECK constraints on risk_level columns in secondary tables
    # For existing PostgreSQL databases that were created before CHECK constraints
    # were added to the CREATE TABLE definitions.  Fresh databases already have them.
    if USE_POSTGRESQL:
        _risk_level_checks = [
            # (table, column, constraint_name)
            ("periodic_reviews", "risk_level", "periodic_reviews_risk_level_check"),
            ("periodic_reviews", "previous_risk_level", "periodic_reviews_prev_risk_level_check"),
            ("periodic_reviews", "new_risk_level", "periodic_reviews_new_risk_level_check"),
            ("sar_reports", "risk_level", "sar_reports_risk_level_check"),
            ("edd_cases", "risk_level", "edd_cases_risk_level_check"),
            ("decision_records", "risk_level", "decision_records_risk_level_check"),
        ]
        for table, column, cname in _risk_level_checks:
            try:
                # Check if constraint already exists
                row = db.execute(
                    "SELECT 1 FROM information_schema.table_constraints "
                    "WHERE table_name=%s AND constraint_name=%s",
                    (table, cname)
                ).fetchone()
                if not row:
                    db.execute(
                        f"ALTER TABLE {table} ADD CONSTRAINT {cname} "
                        f"CHECK({column} IS NULL OR {column} IN "
                        f"('LOW','MEDIUM','HIGH','VERY_HIGH'))"
                    )
                    db.commit()
                    logger.info("Migration v2.11: Added %s on %s.%s", cname, table, column)
            except Exception as e:
                logger.debug("Migration v2.11: %s.%s constraint skipped: %s", table, column, e)
                try:
                    db.rollback()
                except Exception:
                    pass

    # Migration v2.12: Add 'under_review' to applications status CHECK constraint
    # Resolves inconsistency where server.py state transitions reference 'under_review'
    # but the DB CHECK constraint did not include it, causing IntegrityError on transition.
    if USE_POSTGRESQL:
        try:
            constraint_row = db.execute("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'applications_status_check'
                  AND conrelid = 'applications'::regclass
            """).fetchone()
            constraint_def = None
            if constraint_row:
                if isinstance(constraint_row, dict):
                    constraint_def = constraint_row.get("pg_get_constraintdef")
                else:
                    constraint_def = constraint_row[0]

            if constraint_def and "'under_review'" in constraint_def:
                logger.info("Migration v2.12: applications status CHECK constraint already includes 'under_review'")
            else:
                db.execute("ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_status_check")
                db.execute("""ALTER TABLE applications ADD CONSTRAINT applications_status_check
                    CHECK(status IN ('draft','submitted','prescreening_submitted','pricing_review','pricing_accepted',
                    'pre_approval_review','pre_approved','kyc_documents','kyc_submitted','compliance_review','in_review','under_review',
                    'edd_required','approved','rejected','rmi_sent','withdrawn'))""")
                db.commit()
                logger.info("Migration v2.12: Added 'under_review' to applications status CHECK constraint")
        except Exception as e:
            logger.debug("Migration v2.12 status constraint update: %s", e)
            try:
                db.conn.rollback()
            except Exception:
                pass

    # Migration v2.12a: Normalize document verification states and allow
    # the explicit in_progress state used during synchronous verification.
    try:
        db.execute(
            """
            UPDATE documents
            SET verification_status = CASE
                WHEN LOWER(COALESCE(verification_status, '')) IN ('verified','pass','passed','approved')
                    THEN 'verified'
                WHEN LOWER(COALESCE(verification_status, '')) IN ('flagged','warn','warning','review','review_required','manual_review')
                    THEN 'flagged'
                WHEN LOWER(COALESCE(verification_status, '')) IN ('failed','fail','error')
                    THEN 'failed'
                WHEN LOWER(COALESCE(verification_status, '')) IN ('skipped','skip','disabled')
                    THEN 'skipped'
                WHEN LOWER(COALESCE(verification_status, '')) = 'in_progress'
                    THEN 'in_progress'
                ELSE 'pending'
            END
            WHERE verification_status IS NULL
               OR LOWER(COALESCE(verification_status, '')) NOT IN (
                    'pending','in_progress','verified','flagged','failed','skipped'
               )
            """
        )
        if USE_POSTGRESQL:
            constraint_row = db.execute("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'documents_verification_status_check'
                  AND conrelid = 'documents'::regclass
            """).fetchone()
            constraint_def = None
            if constraint_row:
                if isinstance(constraint_row, dict):
                    constraint_def = constraint_row.get("pg_get_constraintdef")
                else:
                    constraint_def = constraint_row[0]
            if not constraint_def or "'in_progress'" not in constraint_def or "'skipped'" not in constraint_def:
                db.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_verification_status_check")
                db.execute("""ALTER TABLE documents ADD CONSTRAINT documents_verification_status_check
                    CHECK(verification_status IN ('pending','in_progress','verified','flagged','failed','skipped'))""")
        db.commit()
        logger.info("Migration v2.12a: documents verification_status state model ready")
    except Exception as e:
        logger.debug("Migration v2.12a documents verification status update: %s", e)
        try:
            db.conn.rollback()
        except Exception:
            pass

    # P12-1 Phase B: the historical v2.13 boot block used to hard-delete every
    # application named "1947 OIL & GAS PLC", including compliance memos, EDD
    # cases, decision records, cascaded evidence, and local files.  Company name
    # is not a safe fixture marker and startup is not a sanctioned deletion
    # workflow.  Keep the discovery signal, but make this permanently
    # report-only.  Deliberate synthetic cleanup must use the explicit guarded
    # non-production fixture workflow instead.
    try:
        target_name = "1947 OIL & GAS PLC"
        rows = db.execute(
            "SELECT id, ref, company_name FROM applications WHERE UPPER(TRIM(company_name)) = ?",
            (target_name.upper(),),
        ).fetchall()
        if rows:
            logger.warning(
                "Migration v2.13 report-only: found %d application(s) named '%s'; "
                "startup deletion is disabled by P12-1. No database or file mutation occurred. "
                "Candidate refs: %s",
                len(rows),
                target_name,
                [row["ref"] if isinstance(row, dict) else row[1] for row in rows],
            )
        else:
            logger.debug("Migration v2.13 report-only: no '%s' applications found.", target_name)
    except Exception as e:
        logger.error("Migration v2.13 report-only check failed: %s", e, exc_info=True)

    # Migration v2.14: Rename 'pep-declaration' → 'pep_declaration' in ai_checks and documents.
    # The doc_type_alias "pep-declaration" was removed from verification_matrix.pep_declaration;
    # canonical doc_type is now 'pep_declaration' (underscore) everywhere.
    try:
        db.execute(
            "UPDATE ai_checks SET doc_type='pep_declaration' WHERE doc_type='pep-declaration'",
        )
        db.execute(
            "UPDATE documents SET doc_type='pep_declaration' WHERE doc_type='pep-declaration'",
        )
        db.commit()
        logger.info("Migration v2.14: renamed pep-declaration → pep_declaration in ai_checks/documents")
    except Exception as e:
        logger.error("Migration v2.14 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.15: Create application_notes table for internal officer notes
    try:
        if db.is_postgres:
            db.execute("""
                CREATE TABLE IF NOT EXISTS application_notes (
                    id SERIAL PRIMARY KEY,
                    application_id TEXT NOT NULL REFERENCES applications(id),
                    user_id TEXT NOT NULL,
                    user_name TEXT,
                    user_role TEXT,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_app_notes_app_id ON application_notes (application_id)")
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS application_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id TEXT NOT NULL REFERENCES applications(id),
                    user_id TEXT NOT NULL,
                    user_name TEXT,
                    user_role TEXT,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_app_notes_app_id ON application_notes (application_id)")
        db.commit()
        logger.info("Migration v2.15: created application_notes table")
    except Exception as e:
        logger.error("Migration v2.15 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.16: Repair malformed risk_config scoring columns
    # Fixes the known corruption where score maps were stored as lists-of-dicts
    # instead of flat dicts (e.g. [{"sme": 2}] → {"sme": 2}).
    try:
        _repair_risk_config_shapes(db)
    except Exception as e:
        logger.error("Migration v2.16 (risk_config repair) failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.17: Add sumsub_unmatched_webhooks table (DLQ for unmatched Sumsub webhooks)
    try:
        if not _safe_table_exists(db, "sumsub_unmatched_webhooks"):
            logger.info("Migration v2.17: Creating sumsub_unmatched_webhooks table")
            if USE_POSTGRESQL:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS sumsub_unmatched_webhooks (
                        id SERIAL PRIMARY KEY,
                        applicant_id TEXT NOT NULL,
                        external_user_id TEXT,
                        event_type TEXT NOT NULL,
                        review_answer TEXT,
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        resolution_note TEXT,
                        resolved_by TEXT,
                        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        resolved_at TIMESTAMP
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS sumsub_unmatched_webhooks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        applicant_id TEXT NOT NULL,
                        external_user_id TEXT,
                        event_type TEXT NOT NULL,
                        review_answer TEXT,
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        resolution_note TEXT,
                        resolved_by TEXT,
                        received_at TEXT DEFAULT (datetime('now')),
                        resolved_at TEXT
                    )
                """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_suw_status ON sumsub_unmatched_webhooks(status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_suw_applicant ON sumsub_unmatched_webhooks(applicant_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_suw_received ON sumsub_unmatched_webhooks(received_at)")
            db.commit()
            logger.info("Migration v2.17: created sumsub_unmatched_webhooks table")
    except Exception as e:
        logger.error("Migration v2.17 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.18: Add before_state and after_state columns to audit_log
    # Enables structured before/after snapshots for critical workflow changes.
    try:
        added = False
        if not _safe_column_exists(db, "audit_log", "before_state"):
            logger.info("Migration v2.18: Adding audit_log.before_state")
            db.execute("ALTER TABLE audit_log ADD COLUMN before_state TEXT")
            added = True
        if not _safe_column_exists(db, "audit_log", "after_state"):
            logger.info("Migration v2.18: Adding audit_log.after_state")
            db.execute("ALTER TABLE audit_log ADD COLUMN after_state TEXT")
            added = True
        if added:
            db.commit()
            logger.info("Migration v2.18: audit_log before/after state columns added")
    except Exception as e:
        logger.error("Migration v2.18 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.19: Add structured dual-approval tracking columns to applications.
    # Moves approval truth out of audit_log free text into proper application fields
    # to eliminate the dual-approval race condition (EX-06).
    try:
        added = False
        if not _safe_column_exists(db, "applications", "first_approver_id"):
            logger.info("Migration v2.19: Adding applications.first_approver_id")
            db.execute("ALTER TABLE applications ADD COLUMN first_approver_id TEXT")
            added = True
        if not _safe_column_exists(db, "applications", "first_approved_at"):
            logger.info("Migration v2.19: Adding applications.first_approved_at")
            db.execute("ALTER TABLE applications ADD COLUMN first_approved_at TIMESTAMP")
            added = True
        if added:
            db.commit()
            logger.info("Migration v2.19: dual-approval tracking columns added")
    except Exception as e:
        logger.error("Migration v2.19 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.20: Add risk recomputation tracking columns to applications (EX-09).
    # risk_computed_at records when risk was last computed/recomputed.
    # risk_config_version records which config version produced the current risk result.
    try:
        added = False
        if not _safe_column_exists(db, "applications", "risk_computed_at"):
            logger.info("Migration v2.20: Adding applications.risk_computed_at")
            db.execute("ALTER TABLE applications ADD COLUMN risk_computed_at TIMESTAMP")
            added = True
        if not _safe_column_exists(db, "applications", "risk_config_version"):
            logger.info("Migration v2.20: Adding applications.risk_config_version")
            db.execute("ALTER TABLE applications ADD COLUMN risk_config_version TEXT")
            added = True
        if added:
            db.commit()
            logger.info("Migration v2.20: risk recomputation tracking columns added")
    except Exception as e:
        logger.error("Migration v2.20 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.21: Change Management module tables.
    # Creates: change_alerts, change_requests, change_request_items,
    # change_request_documents, change_request_reviews, entity_profile_versions.
    try:
        tables_created = []

        if not _safe_table_exists(db, "change_alerts"):
            logger.info("Migration v2.21: Creating change_alerts table")
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE change_alerts (
                        id TEXT PRIMARY KEY,
                        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
                        alert_type TEXT NOT NULL,
                        source_channel TEXT,
                        summary TEXT,
                        detected_changes TEXT,
                        materiality TEXT CHECK(materiality IS NULL OR materiality IN ('tier1','tier2','tier3')),
                        confidence REAL,
                        source_reference TEXT,
                        source_payload TEXT,
                        detected_by TEXT,
                        status TEXT NOT NULL DEFAULT 'new'
                            CHECK(status IN ('new','under_review','awaiting_client_confirmation',
                                             'converted_to_change_request','dismissed',
                                             'resolved_no_change','escalated')),
                        reviewer_id TEXT REFERENCES users(id),
                        reviewer_notes TEXT,
                        reviewed_at TIMESTAMP,
                        converted_request_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_alerts_app ON change_alerts(application_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_alerts_status ON change_alerts(status)")
            else:
                db.execute("""
                    CREATE TABLE change_alerts (
                        id TEXT PRIMARY KEY,
                        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
                        alert_type TEXT NOT NULL,
                        source_channel TEXT,
                        summary TEXT,
                        detected_changes TEXT,
                        materiality TEXT CHECK(materiality IS NULL OR materiality IN ('tier1','tier2','tier3')),
                        confidence REAL,
                        source_reference TEXT,
                        source_payload TEXT,
                        detected_by TEXT,
                        status TEXT NOT NULL DEFAULT 'new'
                            CHECK(status IN ('new','under_review','awaiting_client_confirmation',
                                             'converted_to_change_request','dismissed',
                                             'resolved_no_change','escalated')),
                        reviewer_id TEXT REFERENCES users(id),
                        reviewer_notes TEXT,
                        reviewed_at TEXT,
                        converted_request_id TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_alerts_app ON change_alerts(application_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_alerts_status ON change_alerts(status)")
            tables_created.append("change_alerts")

        if not _safe_table_exists(db, "change_requests"):
            logger.info("Migration v2.21: Creating change_requests table")
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE change_requests (
                        id TEXT PRIMARY KEY,
                        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
                        source TEXT CHECK(source IS NULL OR source IN
                            ('portal_client','backoffice_manual','periodic_review',
                             'ongoing_monitoring','external_alert_conversion','system_admin')),
                        source_channel TEXT,
                        source_alert_id TEXT REFERENCES change_alerts(id),
                        reason TEXT,
                        materiality TEXT CHECK(materiality IS NULL OR materiality IN ('tier1','tier2','tier3')),
                        status TEXT NOT NULL DEFAULT 'draft'
                            CHECK(status IN ('draft','submitted','triage_in_progress',
                                             'pending_information','ready_for_review',
                                             'screening_in_progress','risk_review_required',
                                             'approval_pending','approved','rejected',
                                             'partially_approved','implemented',
                                             'cancelled','superseded')),
                        base_profile_version_id TEXT,
                        result_profile_version_id TEXT,
                        screening_required BOOLEAN DEFAULT FALSE,
                        risk_review_required BOOLEAN DEFAULT FALSE,
                        edd_review_required BOOLEAN DEFAULT FALSE,
                        memo_addendum_hook BOOLEAN DEFAULT FALSE,
                        periodic_review_acceleration_hook BOOLEAN DEFAULT FALSE,
                        pre_change_risk_level TEXT,
                        post_change_risk_level TEXT,
                        precondition_results TEXT,
                        created_by TEXT,
                        submitted_at TIMESTAMP,
                        approved_by TEXT,
                        approved_at TIMESTAMP,
                        decision_notes TEXT,
                        implemented_by TEXT,
                        implemented_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_app ON change_requests(application_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests(status)")
            else:
                db.execute("""
                    CREATE TABLE change_requests (
                        id TEXT PRIMARY KEY,
                        application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
                        source TEXT CHECK(source IS NULL OR source IN
                            ('portal_client','backoffice_manual','periodic_review',
                             'ongoing_monitoring','external_alert_conversion','system_admin')),
                        source_channel TEXT,
                        source_alert_id TEXT REFERENCES change_alerts(id),
                        reason TEXT,
                        materiality TEXT CHECK(materiality IS NULL OR materiality IN ('tier1','tier2','tier3')),
                        status TEXT NOT NULL DEFAULT 'draft'
                            CHECK(status IN ('draft','submitted','triage_in_progress',
                                             'pending_information','ready_for_review',
                                             'screening_in_progress','risk_review_required',
                                             'approval_pending','approved','rejected',
                                             'partially_approved','implemented',
                                             'cancelled','superseded')),
                        base_profile_version_id TEXT,
                        result_profile_version_id TEXT,
                        screening_required INTEGER DEFAULT 0,
                        risk_review_required INTEGER DEFAULT 0,
                        edd_review_required INTEGER DEFAULT 0,
                        memo_addendum_hook INTEGER DEFAULT 0,
                        periodic_review_acceleration_hook INTEGER DEFAULT 0,
                        pre_change_risk_level TEXT,
                        post_change_risk_level TEXT,
                        precondition_results TEXT,
                        created_by TEXT,
                        submitted_at TEXT,
                        approved_by TEXT,
                        approved_at TEXT,
                        decision_notes TEXT,
                        implemented_by TEXT,
                        implemented_at TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_app ON change_requests(application_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests(status)")
            tables_created.append("change_requests")

        if not _safe_table_exists(db, "change_request_items"):
            logger.info("Migration v2.21: Creating change_request_items table")
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE change_request_items (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
                        change_type TEXT NOT NULL,
                        field_name TEXT,
                        old_value TEXT,
                        new_value TEXT,
                        materiality TEXT CHECK(materiality IS NULL OR materiality IN ('tier1','tier2','tier3')),
                        person_action TEXT CHECK(person_action IS NULL OR person_action IN ('add','remove','update')),
                        person_snapshot TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_cr_items_request ON change_request_items(request_id)")
            else:
                db.execute("""
                    CREATE TABLE change_request_items (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
                        change_type TEXT NOT NULL,
                        field_name TEXT,
                        old_value TEXT,
                        new_value TEXT,
                        materiality TEXT CHECK(materiality IS NULL OR materiality IN ('tier1','tier2','tier3')),
                        person_action TEXT CHECK(person_action IS NULL OR person_action IN ('add','remove','update')),
                        person_snapshot TEXT,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_cr_items_request ON change_request_items(request_id)")
            tables_created.append("change_request_items")

        if not _safe_table_exists(db, "change_request_documents"):
            logger.info("Migration v2.21: Creating change_request_documents table")
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE change_request_documents (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
                        item_id TEXT REFERENCES change_request_items(id),
                        doc_name TEXT NOT NULL,
                        doc_type TEXT,
                        file_path TEXT NOT NULL,
                        s3_key TEXT,
                        uploaded_by TEXT REFERENCES users(id),
                        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_cr_docs_request ON change_request_documents(request_id)")
            else:
                db.execute("""
                    CREATE TABLE change_request_documents (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
                        item_id TEXT REFERENCES change_request_items(id),
                        doc_name TEXT NOT NULL,
                        doc_type TEXT,
                        file_path TEXT NOT NULL,
                        s3_key TEXT,
                        uploaded_by TEXT REFERENCES users(id),
                        uploaded_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_cr_docs_request ON change_request_documents(request_id)")
            tables_created.append("change_request_documents")

        if not _safe_table_exists(db, "change_request_reviews"):
            logger.info("Migration v2.21: Creating change_request_reviews table")
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE change_request_reviews (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
                        reviewer_id TEXT REFERENCES users(id),
                        reviewer_role TEXT,
                        decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','request_info','escalate')),
                        decision_notes TEXT,
                        reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_cr_reviews_request ON change_request_reviews(request_id)")
            else:
                db.execute("""
                    CREATE TABLE change_request_reviews (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
                        reviewer_id TEXT REFERENCES users(id),
                        reviewer_role TEXT,
                        decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','request_info','escalate')),
                        decision_notes TEXT,
                        reviewed_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_cr_reviews_request ON change_request_reviews(request_id)")
            tables_created.append("change_request_reviews")

        if not _safe_table_exists(db, "entity_profile_versions"):
            logger.info("Migration v2.21: Creating entity_profile_versions table")
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE entity_profile_versions (
                        id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        version_number INTEGER NOT NULL DEFAULT 1,
                        is_current BOOLEAN DEFAULT TRUE,
                        profile_snapshot TEXT NOT NULL,
                        change_request_id TEXT REFERENCES change_requests(id),
                        created_by TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_epv_app ON entity_profile_versions(application_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_epv_current ON entity_profile_versions(application_id, is_current)")
            else:
                db.execute("""
                    CREATE TABLE entity_profile_versions (
                        id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        version_number INTEGER NOT NULL DEFAULT 1,
                        is_current INTEGER DEFAULT 1,
                        profile_snapshot TEXT NOT NULL,
                        change_request_id TEXT REFERENCES change_requests(id),
                        created_by TEXT,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_epv_app ON entity_profile_versions(application_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_epv_current ON entity_profile_versions(application_id, is_current)")
            tables_created.append("entity_profile_versions")

        if tables_created:
            db.commit()
            logger.info("Migration v2.21: Change Management tables created: %s", ", ".join(tables_created))
        else:
            logger.info("Migration v2.21: All Change Management tables already exist")

    except Exception as e:
        logger.error("Migration v2.21 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.22: Add risk_escalations column to applications.
    # Stores JSON array of escalation reasons from compute_risk_score()
    # (e.g. ["floor_rule_sanctioned_country:iran", "sub_factor_score_4"]).
    # Used by validation_engine to distinguish legitimate risk elevation
    # from genuine memo/risk contradictions.
    try:
        if not _safe_column_exists(db, "applications", "risk_escalations"):
            db.execute("ALTER TABLE applications ADD COLUMN risk_escalations TEXT DEFAULT '[]'")
            db.commit()
            logger.info("Migration v2.22: Added risk_escalations column to applications")
        else:
            logger.info("Migration v2.22: risk_escalations column already exists")
    except Exception as e:
        logger.error("Migration v2.22 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.23: Add risk elevation tracking columns to applications.
    # Stores base_risk_level (score-based), final_risk_level (post-elevation),
    # and elevation_reason_text (human-readable explanation of any elevation).
    try:
        cols_added = []
        if not _safe_column_exists(db, "applications", "base_risk_level"):
            db.execute("ALTER TABLE applications ADD COLUMN base_risk_level TEXT")
            cols_added.append("base_risk_level")
        if not _safe_column_exists(db, "applications", "final_risk_level"):
            db.execute("ALTER TABLE applications ADD COLUMN final_risk_level TEXT")
            cols_added.append("final_risk_level")
        if not _safe_column_exists(db, "applications", "elevation_reason_text"):
            db.execute("ALTER TABLE applications ADD COLUMN elevation_reason_text TEXT DEFAULT ''")
            cols_added.append("elevation_reason_text")
        if cols_added:
            db.commit()
            logger.info("Migration v2.23: Added columns %s to applications", cols_added)
        else:
            logger.info("Migration v2.23: elevation tracking columns already exist")
    except Exception as e:
        logger.error("Migration v2.23 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.24: Webhook idempotency guard table (EX-04).
    # Prevents duplicate processing of Sumsub webhook deliveries by recording
    # a canonical event key on first receipt.  A UNIQUE constraint on event_digest
    # ensures that re-deliveries are safely short-circuited before any mutating
    # logic (audit_log, application update, DLQ) runs.
    try:
        if not _safe_table_exists(db, "webhook_processed_events"):
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE webhook_processed_events (
                        id          SERIAL PRIMARY KEY,
                        event_digest TEXT NOT NULL UNIQUE,
                        event_type  TEXT NOT NULL DEFAULT '',
                        applicant_id TEXT NOT NULL DEFAULT '',
                        external_user_id TEXT NOT NULL DEFAULT '',
                        review_answer TEXT NOT NULL DEFAULT '',
                        received_at TEXT NOT NULL DEFAULT ''
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS webhook_processed_events (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_digest TEXT NOT NULL UNIQUE,
                        event_type  TEXT NOT NULL DEFAULT '',
                        applicant_id TEXT NOT NULL DEFAULT '',
                        external_user_id TEXT NOT NULL DEFAULT '',
                        review_answer TEXT NOT NULL DEFAULT '',
                        received_at TEXT NOT NULL DEFAULT ''
                    )
                """)
            db.commit()
            logger.info("Migration v2.24: Created webhook_processed_events table")
        else:
            logger.info("Migration v2.24: webhook_processed_events already exists")
    except Exception as e:
        logger.error("Migration v2.24 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.24a: Ensure ComplyAdvantage monitoring alert identity columns.
    #
    # Long-lived staging databases can have a legacy monitoring_alerts table
    # that predates provider/case_identifier.  CREATE TABLE IF NOT EXISTS does
    # not add missing columns, so the provider/case unique index must be created
    # only after this inline repair has run.
    try:
        cols_added = []
        if not _safe_column_exists(db, "monitoring_alerts", "provider"):
            db.execute("ALTER TABLE monitoring_alerts ADD COLUMN provider TEXT")
            cols_added.append("provider")
        if not _safe_column_exists(db, "monitoring_alerts", "case_identifier"):
            db.execute("ALTER TABLE monitoring_alerts ADD COLUMN case_identifier TEXT")
            cols_added.append("case_identifier")

        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_alerts_provider_case
            ON monitoring_alerts(provider, case_identifier)
            WHERE provider IS NOT NULL AND case_identifier IS NOT NULL
        """)
        db.commit()
        if cols_added:
            logger.info(
                "Migration v2.24a: Added monitoring_alerts columns %s and ensured provider/case index",
                cols_added,
            )
        else:
            logger.info("Migration v2.24a: monitoring_alerts provider/case index ensured")
    except Exception as e:
        logger.error("Migration v2.24a failed: %s", e, exc_info=True)
        try:
            db.conn.rollback()
        except Exception:
            pass

    # Migration v2.24b: Add monitoring_alerts provenance for CA historical backfill.
    try:
        cols_added = []
        if not _safe_column_exists(db, "monitoring_alerts", "discovered_via"):
            db.execute(
                "ALTER TABLE monitoring_alerts ADD COLUMN discovered_via TEXT NOT NULL "
                "DEFAULT 'webhook_live'"
            )
            cols_added.append("discovered_via")
        if db.is_postgres:
            _replace_postgres_column_check_constraint(
                db,
                table="monitoring_alerts",
                column="discovered_via",
                constraint_name="monitoring_alerts_discovered_via_check",
                allowed_values=MONITORING_ALERT_DISCOVERED_VIA_VALUES,
            )
        if not _safe_column_exists(db, "monitoring_alerts", "discovered_at"):
            db.execute("ALTER TABLE monitoring_alerts ADD COLUMN discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            cols_added.append("discovered_at")
        if not _safe_column_exists(db, "monitoring_alerts", "backfill_run_id"):
            db.execute("ALTER TABLE monitoring_alerts ADD COLUMN backfill_run_id TEXT")
            cols_added.append("backfill_run_id")
        db.commit()
        if cols_added:
            logger.info("Migration v2.24b: Added monitoring_alerts provenance columns %s", cols_added)
        else:
            logger.info("Migration v2.24b: monitoring_alerts provenance columns already exist")
    except Exception as e:
        logger.error("Migration v2.24b failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.24d: CA webhook idempotency and structured monitoring evidence.
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS complyadvantage_webhook_deliveries (
                webhook_id TEXT PRIMARY KEY,
                first_received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                webhook_type TEXT,
                case_identifier TEXT,
                customer_identifier TEXT,
                processing_status TEXT NOT NULL DEFAULT 'processing',
                processing_result TEXT,
                failure_reason TEXT,
                trace_id TEXT,
                payload_json TEXT,
                alert_identifiers_json TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TIMESTAMP,
                processed_at TIMESTAMP
            )
        """)
        for column, ddl in (
            ("payload_json", "ALTER TABLE complyadvantage_webhook_deliveries ADD COLUMN payload_json TEXT"),
            ("alert_identifiers_json", "ALTER TABLE complyadvantage_webhook_deliveries ADD COLUMN alert_identifiers_json TEXT"),
            ("retry_count", "ALTER TABLE complyadvantage_webhook_deliveries ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"),
            ("next_retry_at", "ALTER TABLE complyadvantage_webhook_deliveries ADD COLUMN next_retry_at TIMESTAMP"),
        ):
            if not _safe_column_exists(db, "complyadvantage_webhook_deliveries", column):
                db.execute(ddl)
        db.execute("CREATE INDEX IF NOT EXISTS idx_ca_webhook_deliveries_status ON complyadvantage_webhook_deliveries(processing_status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ca_webhook_deliveries_case ON complyadvantage_webhook_deliveries(case_identifier)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS monitoring_alert_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitoring_alert_id INTEGER NOT NULL,
                application_id TEXT,
                provider TEXT NOT NULL,
                case_identifier TEXT,
                alert_identifier TEXT,
                match_identifier TEXT,
                risk_identifier TEXT,
                profile_identifier TEXT,
                evidence_type TEXT,
                matched_subject_name TEXT,
                relationship_to_client TEXT,
                match_category TEXT,
                risk_indicator TEXT,
                match_confidence TEXT,
                source_title TEXT,
                source_name TEXT,
                source_url TEXT,
                source_url_available BOOLEAN DEFAULT false,
                source_url_unavailable_reason TEXT,
                publication_date TEXT,
                snippet TEXT,
                provider_case_url TEXT,
                evidence_json TEXT,
                raw_provider_reference TEXT,
                evidence_status TEXT DEFAULT 'fetched',
                evidence_hash TEXT NOT NULL,
                fetched_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(monitoring_alert_id, evidence_hash)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_alert ON monitoring_alert_evidence(monitoring_alert_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_case ON monitoring_alert_evidence(provider, case_identifier)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_app ON monitoring_alert_evidence(application_id)")
        db.commit()
        logger.info("Migration v2.24d: CA webhook delivery and monitoring evidence tables ensured")
    except Exception as e:
        logger.error("Migration v2.24d failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.24c: Add durable D2 provider-pair comparison artifacts.
    try:
        if db.is_postgres:
            db.execute("""
                CREATE TABLE IF NOT EXISTS screening_provider_comparisons (
                    id SERIAL PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    primary_provider TEXT NOT NULL,
                    shadow_provider TEXT NOT NULL,
                    comparison_kind TEXT NOT NULL DEFAULT 'screening_shadow',
                    primary_normalized_record_id INTEGER,
                    shadow_normalized_record_id INTEGER,
                    mismatch_class TEXT NOT NULL,
                    comparison_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS screening_provider_comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    primary_provider TEXT NOT NULL,
                    shadow_provider TEXT NOT NULL,
                    comparison_kind TEXT NOT NULL DEFAULT 'screening_shadow',
                    primary_normalized_record_id INTEGER,
                    shadow_normalized_record_id INTEGER,
                    mismatch_class TEXT NOT NULL,
                    comparison_json TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_provider_comparisons_app "
            "ON screening_provider_comparisons(application_id)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_comparisons_app_pair "
            "ON screening_provider_comparisons(application_id, primary_provider, shadow_provider, comparison_kind)"
        )
        db.commit()
        logger.info("Migration v2.24c: screening_provider_comparisons table ensured")
    except Exception as e:
        logger.error("Migration v2.24c failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.25: Add approval_reason column to compliance_memos (EX-06).
    # Stores the mandatory reason when a senior approver approves a memo with
    # validation_status == 'pass_with_fixes'.
    try:
        if not _safe_column_exists(db, "compliance_memos", "approval_reason"):
            db.execute("ALTER TABLE compliance_memos ADD COLUMN approval_reason TEXT")
            db.commit()
            logger.info("Migration v2.25: Added approval_reason column to compliance_memos")
        else:
            logger.info("Migration v2.25: approval_reason column already exists")
    except Exception as e:
        logger.error("Migration v2.25 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.26: Add reviewer_role column to documents table (EX-06).
    # Stores the role of the officer who reviewed the document, enabling
    # the senior-officer override path for flagged documents.
    try:
        if not _safe_column_exists(db, "documents", "reviewer_role"):
            db.execute("ALTER TABLE documents ADD COLUMN reviewer_role TEXT")
            db.commit()
            logger.info("Migration v2.26: Added reviewer_role column to documents")
        else:
            logger.info("Migration v2.26: reviewer_role column already exists")
    except Exception as e:
        logger.error("Migration v2.26 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.26a: Add structured screening disposition controls.
    # These columns make screening clear/escalate/follow-up decisions
    # audit-grade: each disposition has a code/rationale and sensitive
    # clears can require a second distinct officer sign-off.
    try:
        added = []
        screening_review_cols = [
            ("disposition_code", "TEXT"),
            ("rationale", "TEXT"),
            ("sensitivity_flags", "TEXT DEFAULT '[]'"),
            ("requires_four_eyes", "BOOLEAN DEFAULT FALSE" if db.is_postgres else "INTEGER DEFAULT 0"),
            ("second_reviewer_id", "TEXT"),
            ("second_reviewer_name", "TEXT"),
            ("second_disposition_code", "TEXT"),
            ("second_rationale", "TEXT"),
            ("second_reviewed_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
        ]
        for column_name, column_type in screening_review_cols:
            if not _safe_column_exists(db, "screening_reviews", column_name):
                db.execute(f"ALTER TABLE screening_reviews ADD COLUMN {column_name} {column_type}")
                added.append(column_name)
        if added:
            db.commit()
            logger.info("Migration v2.26a: Added screening_reviews columns %s", added)
        else:
            logger.info("Migration v2.26a: screening_reviews disposition columns already exist")
    except Exception as e:
        logger.error("Migration v2.26a failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.27: Drop FK constraints on created_by/approved_by/implemented_by
    # in change_requests and entity_profile_versions.
    # These columns can hold either officer user IDs (from users table) or
    # client IDs (from clients table) when portal clients create change requests.
    # The FK to users(id) causes a ForeignKeyViolation in PostgreSQL for client-
    # created requests.  SQLite does not enforce FKs by default, so this only
    # manifests in production (PostgreSQL).
    if db.is_postgres:
        _fk_targets = [
            ("change_requests", "created_by"),
            ("change_requests", "approved_by"),
            ("change_requests", "implemented_by"),
            ("entity_profile_versions", "created_by"),
        ]
        for table, column in _fk_targets:
            try:
                # Find the auto-generated constraint name for this FK
                rows = db.execute(
                    """SELECT conname FROM pg_constraint
                       WHERE conrelid = %s::regclass
                         AND contype = 'f'
                         AND conkey @> ARRAY[(
                             SELECT attnum FROM pg_attribute
                             WHERE attrelid = %s::regclass AND attname = %s
                         )]""",
                    (table, table, column),
                ).fetchall()
                for row in rows:
                    constraint_name = row["conname"]
                    # Validate constraint name contains only safe identifier chars
                    import re
                    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', constraint_name):
                        logger.warning(
                            "Migration v2.27: Skipping invalid constraint name: %s",
                            constraint_name,
                        )
                        continue
                    db.execute(
                        f"ALTER TABLE {table} DROP CONSTRAINT {constraint_name}"
                    )
                    db.commit()
                    logger.info(
                        "Migration v2.27: Dropped FK constraint %s on %s.%s",
                        constraint_name, table, column,
                    )
            except Exception as e:
                logger.info(
                    "Migration v2.27: No FK to drop on %s.%s (%s)", table, column, e
                )
                try:
                    db.rollback()
                except Exception:
                    pass

    # Migration v2.28: Add inputs_updated_at column to applications.
    # This column tracks only substantive application-input mutations (data edits,
    # screening reruns, change-management field updates).  Operational workflow
    # writes (approval-state, status, assignment) update only updated_at, NOT
    # inputs_updated_at.  The memo-staleness gate compares memo.created_at
    # against inputs_updated_at so that first-approval writes do not falsely
    # retrigger stale-memo blocking.
    if not _safe_column_exists(db, "applications", "inputs_updated_at"):
        try:
            # Step 1: Add column WITHOUT a default so existing rows get NULL.
            # This ensures the backfill in Step 2 actually matches existing rows
            # (PostgreSQL would otherwise compute CURRENT_TIMESTAMP for all
            # existing rows, skipping the backfill).
            if db.is_postgres:
                db.execute(
                    "ALTER TABLE applications ADD COLUMN inputs_updated_at TIMESTAMP"
                )
            else:
                db.execute(
                    "ALTER TABLE applications ADD COLUMN inputs_updated_at TEXT"
                )
            # Step 2: Backfill from updated_at so existing memos retain correct
            # freshness semantics.
            db.execute(
                "UPDATE applications SET inputs_updated_at = updated_at "
                "WHERE inputs_updated_at IS NULL"
            )
            # Step 3: Set the column default for future inserts.
            if db.is_postgres:
                db.execute(
                    "ALTER TABLE applications "
                    "ALTER COLUMN inputs_updated_at SET DEFAULT CURRENT_TIMESTAMP"
                )
            # SQLite does not support ALTER COLUMN SET DEFAULT; the default is
            # defined in the CREATE TABLE schema for new databases, and for
            # migrated databases new INSERTs must supply the value explicitly
            # or rely on the application layer.
            db.commit()
            logger.info("Migration v2.28: Added and backfilled applications.inputs_updated_at")
        except Exception as e:
            logger.info("Migration v2.28: inputs_updated_at already exists or failed: %s", e)
            try:
                db.rollback()
            except Exception:
                pass

    # Migration v2.29: Add is_fixture column to applications and mark rogue
    # historical test rows.
    #
    # Background: Eight historical test rows exist in demo/staging with normal
    # UUID-like IDs that bypass the canonical ``id LIKE 'f1xed%'`` fixture
    # filter introduced in Priority D.  They are:
    #
    #   ARF-2026-100454  EX06 DualApproval Test Corp
    #   ARF-2026-100456  EX06 Validation TestCo Ltd
    #   ARF-2026-100455  HighRisk Dual Approval Test Ltd
    #   ARF-2026-100421  Pipeline Test Corp Ltd
    #   ARF-2026-100424  Portal Audit Test Ltd
    #   ARF-2026-100430  Probe Test Co
    #   ARF-2026-100428  test 2
    #   ARF-2026-100427  test [QA-R10-mnyuuv7q]
    #
    # These were created before the ``f1xed`` namespace was established.
    #
    # Fix: Add an explicit ``is_fixture`` boolean marker column, backfill
    # all existing ``f1xed%`` rows (belt-and-suspenders), and mark the 8
    # rogue rows by their stable ``ref`` value.  The fixture_filter module
    # combines both signals: ``id LIKE 'f1xed%' OR is_fixture``.
    #
    # IMPORTANT — environment guard on the rogue-ref UPDATE:
    # The rogue refs share the same sequential ref range as the first real
    # customer applications created in any environment (since
    # _REF_BASE_NUMBER = 100421).  Unconditionally marking these refs as
    # is_fixture=1 on every startup will hide real production/pilot
    # applications from every Back Office query.
    # The rogue-ref UPDATE therefore runs ONLY in demo/staging environments
    # (where those rows are known test data).  In all other environments
    # (production, development, testing) the code instead resets any rows
    # that were incorrectly marked by a previous migration run, restoring
    # Back Office visibility for real applications.
    _ROGUE_FIXTURE_REFS = (
        "ARF-2026-100454",  # EX06 DualApproval Test Corp
        "ARF-2026-100456",  # EX06 Validation TestCo Ltd
        "ARF-2026-100455",  # HighRisk Dual Approval Test Ltd
        "ARF-2026-100421",  # Pipeline Test Corp Ltd
        "ARF-2026-100424",  # Portal Audit Test Ltd
        "ARF-2026-100430",  # Probe Test Co
        "ARF-2026-100428",  # test 2
        "ARF-2026-100427",  # test [QA-R10-mnyuuv7q]
        "ARF-2026-900372",  # Smoke Holdco Ltd (staging smoke row missed by name patterns)
    )
    # Dialect-aware truthy/falsy literals for the is_fixture column.
    # PostgreSQL declares the column as BOOLEAN, SQLite as INTEGER.  Writing
    # an integer literal to a BOOLEAN column raises psycopg2 DatatypeMismatch
    # and rolls ba which would also
    # undo the ADD COLUMN and leave every fixture-filter WHERE clause
    # broken.  Using TRUE/FALSE on Postgres and 1/0 on SQLite is safe in
    # both dialects.
    _TRUE_LIT = "TRUE" if db.is_postgres else "1"
    _FALSE_LIT = "FALSE" if db.is_postgres else "0"

    # Step 1 — schema change in its own transaction.  Committing before the
    # backfill means a later UPDATE failure cannot rollback the ADD COLUMN
    # and leave the DB without the column that fixture_filter queries
    # depend on.
    try:
        if not _safe_column_exists(db, "applications", "is_fixture"):
            if db.is_postgres:
                db.execute(
                    "ALTER TABLE applications ADD COLUMN is_fixture BOOLEAN DEFAULT FALSE NOT NULL"
                )
            else:
                db.execute(
                    "ALTER TABLE applications ADD COLUMN is_fixture INTEGER DEFAULT 0 NOT NULL"
                )
            db.commit()
            logger.info("Migration v2.29: Added is_fixture column to applications")
    except Exception as e:
        logger.error(
            "Migration v2.29 schema step failed: %s", e, exc_info=True
        )
        try:
            db.conn.rollback()
        except Exception:
            pass
        # Without the column the backfill cannot run; bail out of v2.29.
        return

    # Step 2 — data backfill in its own transaction.  A failure here no
    # longer takes the schema down with it.
    try:
        # Belt-and-suspenders: mark all existing f1xed% rows as is_fixture.
        # This is safe in every environment: real UUID IDs can never start
        # with 'f1xed' (it contains the letter 'x' absent from hex digits).
        db.execute(
            f"UPDATE applications SET is_fixture = {_TRUE_LIT} WHERE id LIKE ?",
            ("f1xed%",),
        )
        # Rogue-ref marking is ENVIRONMENT-SCOPED to prevent hiding real data.
        if _ROGUE_FIXTURE_REFS:
            placeholders = ",".join(["?"] * len(_ROGUE_FIXTURE_REFS))
            try:
                from environment import (
                    is_demo as _is_demo_env,
                    is_staging as _is_staging_env,
                )
                _is_demo_or_staging_env = _is_demo_env() or _is_staging_env()
            except Exception as _env_err:
                logger.warning(
                    "Migration v2.29: could not determine environment from environment.py"
                    " (%s) — defaulting to non-demo/staging (rogue refs will be reset)",
                    _env_err,
                )
                _is_demo_or_staging_env = False
            if _is_demo_or_staging_env:
                # demo/staging: mark the 8 known rogue test rows as fixtures.
                db.execute(
                    f"UPDATE applications SET is_fixture = {_TRUE_LIT} WHERE ref IN ({placeholders})",
                    list(_ROGUE_FIXTURE_REFS),
                )
                # NB: db.execute() returns the DBConnection wrapper, not a cursor.
                # rowcount lives on the underlying cursor (db._cursor); read it via
                # getattr so this stays safe across DBAPI adapters.
                rows_updated = getattr(db._cursor, "rowcount", -1)
                logger.info(
                    "Migration v2.29: Marked %d rogue historical test row(s) as is_fixture"
                    " (demo/staging environment)",
                    rows_updated or 0,
                )
            else:
                # production / development / testing: restore any real
                # applications that were incorrectly marked as is_fixture=TRUE
                # by a previous migration run of this block.  Only rows
                # whose IDs do NOT match the f1xed% namespace are reset;
                # genuine seeded rows (f1xed%) remain correctly marked.
                db.execute(
                    f"UPDATE applications SET is_fixture = {_FALSE_LIT} "
                    f"WHERE ref IN ({placeholders}) AND id NOT LIKE ?",
                    list(_ROGUE_FIXTURE_REFS) + ["f1xed%"],
                )
                # NB: db.execute() returns the DBConnection wrapper, not a cursor.
                # rowcount lives on the underlying cursor (db._cursor); read it via
                # getattr so this stays safe across DBAPI adapters.
                rows_reset = getattr(db._cursor, "rowcount", -1)
                if rows_reset:
                    logger.warning(
                        "Migration v2.29: Restored %d real application(s) incorrectly "
                        "marked as is_fixture — rogue-ref reset applied "
                        "(non-demo/staging environment)",
                        rows_reset,
                    )
        db.commit()
        logger.info(
            "Migration v2.29: is_fixture column ensured and backfill complete"
        )
    except Exception as e:
        logger.error("Migration v2.29 backfill failed: %s", e, exc_info=True)
        try:
            db.conn.rollback()
        except Exception:
            pass

    # Migration v2.30: Structured RMI request tracking.
    #
    # Older "request documents" decisions only wrote free text into
    # client_notifications.documents_list.  The RMI loop needs durable
    # request/item rows so the portal can render requested slots and the
    # back office can track line-item fulfillment.
    try:
        if not _safe_column_exists(db, "client_notifications", "rmi_request_id"):
            db.execute("ALTER TABLE client_notifications ADD COLUMN rmi_request_id TEXT")

        if not _safe_table_exists(db, "rmi_requests"):
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE rmi_requests (
                        id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        client_id TEXT REFERENCES clients(id),
                        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending_review','partially_fulfilled','fulfilled','cancelled')),
                        reason TEXT NOT NULL,
                        deadline TEXT NOT NULL,
                        created_by TEXT REFERENCES users(id),
                        created_by_name TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        fulfilled_at TIMESTAMP
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS rmi_requests (
                        id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        client_id TEXT REFERENCES clients(id),
                        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending_review','partially_fulfilled','fulfilled','cancelled')),
                        reason TEXT NOT NULL,
                        deadline TEXT NOT NULL,
                        created_by TEXT REFERENCES users(id),
                        created_by_name TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now')),
                        fulfilled_at TEXT
                    )
                """)

        if not _safe_table_exists(db, "rmi_request_items"):
            if db.is_postgres:
                db.execute("""
                    CREATE TABLE rmi_request_items (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES rmi_requests(id) ON DELETE CASCADE,
                        doc_type TEXT NOT NULL,
                        label TEXT NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','uploaded','accepted','rejected')),
                        document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                        uploaded_at TIMESTAMP,
                        reviewed_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS rmi_request_items (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES rmi_requests(id) ON DELETE CASCADE,
                        doc_type TEXT NOT NULL,
                        label TEXT NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','uploaded','accepted','rejected')),
                        document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                        uploaded_at TEXT,
                        reviewed_at TEXT,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_rmi_requests_app ON rmi_requests(application_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rmi_requests_client ON rmi_requests(client_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rmi_requests_status ON rmi_requests(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rmi_items_request ON rmi_request_items(request_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rmi_items_doc ON rmi_request_items(document_id)")
        if db.is_postgres:
            _replace_postgres_column_check_constraint(
                db,
                table="rmi_requests",
                column="status",
                constraint_name="rmi_requests_status_check",
                allowed_values=RMI_REQUEST_STATUS_VALUES,
            )
        db.commit()
        logger.info("Migration v2.30: Structured RMI tables and notification linkage ensured")
    except Exception as e:
        logger.error("Migration v2.30 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.31: Ensure long-lived compliance_memos tables have the
    # memo-integrity metadata columns used for idempotent generation and PDF
    # evidence. Fresh schemas already include these columns; this backfills
    # older staging/demo databases without rewriting existing memo rows.
    try:
        added = False
        memo_columns = [
            ("version", "INTEGER DEFAULT 1"),
            ("quality_score", "REAL DEFAULT 0"),
            (
                "validation_status",
                "TEXT DEFAULT 'pending' CHECK(validation_status IN ('pending','pass','pass_with_fixes','fail'))",
            ),
            ("validation_issues", "TEXT DEFAULT '[]'"),
            ("validation_run_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
            ("memo_version", "TEXT DEFAULT '1.0'"),
            ("raw_output_hash", "TEXT"),
            ("supervisor_status", "TEXT DEFAULT 'pending'"),
            ("supervisor_summary", "TEXT"),
            ("supervisor_contradictions", "TEXT DEFAULT '[]'"),
            ("rule_violations", "TEXT DEFAULT '[]'"),
            ("rule_engine_status", "TEXT DEFAULT 'pending'"),
            ("blocked", "BOOLEAN DEFAULT FALSE" if db.is_postgres else "INTEGER DEFAULT 0"),
            ("block_reason", "TEXT"),
            ("is_stale", "BOOLEAN DEFAULT FALSE" if db.is_postgres else "INTEGER DEFAULT 0"),
            ("stale_reason", "TEXT"),
            ("stale_reasons", "TEXT DEFAULT '[]'"),
            ("stale_trigger", "TEXT"),
            ("stale_marked_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
            ("pdf_generated_at", "TIMESTAMP" if db.is_postgres else "TEXT"),
        ]
        for column, definition in memo_columns:
            if not _safe_column_exists(db, "compliance_memos", column):
                db.execute(f"ALTER TABLE compliance_memos ADD COLUMN {column} {definition}")
                added = True
        for index_name, column in (
            ("idx_compliance_memos_application_id", "application_id"),
            ("idx_compliance_memos_review_status", "review_status"),
            ("idx_compliance_memos_validation_status", "validation_status"),
            ("idx_compliance_memos_blocked", "blocked"),
            ("idx_compliance_memos_created_at", "created_at"),
        ):
            if _safe_column_exists(db, "compliance_memos", column):
                db.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON compliance_memos({column})")
        db.commit()
        if added:
            logger.info("Migration v2.31: Ensured compliance_memos memo-integrity columns")
    except Exception as e:
        logger.error("Migration v2.31 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.31b (PR-20): one-time backfill of compliance_memos.blocked /
    # block_reason from the persisted memo_data JSON (see
    # _backfill_pr20_memo_blocked).
    try:
        _backfill_pr20_memo_blocked(db)
    except Exception as e:
        logger.error("Migration v2.31b (PR-20) blocked backfill failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.32: Application-specific generated Enhanced / EDD requirements.
    #
    # Note: v2.32 is the db.py inline migration sequence. The matching file
    # migration is migration_022_application_enhanced_requirements.sql in the
    # schema_version runner sequence.
    #
    # Step 2 persists rule snapshots per application without creating RMI
    # requests, portal prompts, memo output, or approval blockers.
    try:
        if not _safe_table_exists(db, "application_enhanced_requirements"):
            _ensure_application_enhanced_requirements_table(db)
            db.commit()
            logger.info("Migration v2.32: Created application_enhanced_requirements table")
        else:
            logger.info("Migration v2.32: application_enhanced_requirements table already exists")
    except Exception as e:
        logger.error("Migration v2.32 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.33: Client fulfilment fields for requested Enhanced / EDD
    # requirements. The matching file migration is
    # migration_023_application_enhanced_requirement_fulfilment.sql.
    #
    # This is additive only: it stores client text responses and timestamps for
    # Step 5C fulfilment without changing approval gates, memo output, RMI, EDD
    # case state, screening, risk thresholds, or standard KYC upload behaviour.
    try:
        _ensure_application_enhanced_requirement_fulfilment_columns(db)
        db.commit()
        logger.info(
            "Migration v2.33: Ensured application_enhanced_requirements client fulfilment columns"
        )
    except Exception as e:
        logger.error("Migration v2.33 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.34: Compliance-grade document slot versioning.
    #
    # One logical upload slot may have historical document rows, but exactly
    # one row should be current evidence. This additive migration backfills the
    # versioning columns and repairs legacy duplicate active rows.
    try:
        repair_document_current_versions(db)
        db.commit()
        logger.info("Migration v2.34: Ensured document slot versioning/current-document repair")
    except Exception as e:
        logger.error("Migration v2.34 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.35: Persist document expiry metadata used by ongoing
    # monitoring and periodic review document-health checks.
    try:
        _ensure_document_health_columns(db)
        db.commit()
        logger.info("Migration v2.35: Ensured document expiry metadata columns")
    except Exception as e:
        logger.error("Migration v2.35 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            logger.warning("Rollback after migration v2.35 failed", exc_info=True)

    # Migration v2.36: Officer correction audit store.
    # Preserves client-submitted values alongside officer-verified corrections
    # without replacing the existing change-management workflow.
    try:
        if not _safe_table_exists(db, "application_corrections"):
            if db.is_postgres:
                db.execute(
                    """
                    CREATE TABLE application_corrections (
                        id BIGSERIAL PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        target_type TEXT NOT NULL,
                        target_id TEXT,
                        subject_type TEXT,
                        field_scope TEXT,
                        materiality TEXT NOT NULL,
                        correction_reason TEXT NOT NULL,
                        evidence_source TEXT,
                        correction_note TEXT,
                        correction_source TEXT,
                        before_state TEXT NOT NULL,
                        after_state TEXT NOT NULL,
                        downstream_state TEXT DEFAULT '{}',
                        corrected_by TEXT,
                        corrected_by_name TEXT,
                        corrected_by_role TEXT,
                        corrected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            else:
                db.execute(
                    """
                    CREATE TABLE application_corrections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                        target_type TEXT NOT NULL,
                        target_id TEXT,
                        subject_type TEXT,
                        field_scope TEXT,
                        materiality TEXT NOT NULL,
                        correction_reason TEXT NOT NULL,
                        evidence_source TEXT,
                        correction_note TEXT,
                        correction_source TEXT,
                        before_state TEXT NOT NULL,
                        after_state TEXT NOT NULL,
                        downstream_state TEXT DEFAULT '{}',
                        corrected_by TEXT,
                        corrected_by_name TEXT,
                        corrected_by_role TEXT,
                        corrected_at TEXT DEFAULT (datetime('now'))
                    )
                    """
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_application_corrections_app "
                "ON application_corrections(application_id, corrected_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_application_corrections_target "
                "ON application_corrections(target_type, target_id)"
            )
            db.commit()
            logger.info("Migration v2.36: Created application_corrections table")
        else:
            logger.info("Migration v2.36: application_corrections table already exists")
    except Exception as e:
        logger.error("Migration v2.36 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.37: Canonical periodic review Phase 1 ownership fields.
    # Keeps periodic_reviews as the single review-state source of truth and
    # adds evidence-link storage without creating a duplicate document store.
    try:
        _ensure_periodic_review_phase1_schema(db)
        db.commit()
        logger.info(
            "Migration v2.37: Ensured canonical periodic review fields and evidence links"
        )
    except Exception as e:
        logger.error("Migration v2.37 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38: Async verification jobs foundation (dark behind
    # FF_ASYNC_VERIFY).  This is schema-only and does not change active
    # synchronous verification behaviour while the flag remains false.
    try:
        _ensure_verification_jobs_schema(db)
        db.commit()
        logger.info("Migration v2.38: Ensured async verification jobs schema")
    except Exception as e:
        logger.error("Migration v2.38 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38s: async application screening jobs.  These jobs let the
    # submit endpoint persist durable client state before live provider polling.
    try:
        _ensure_screening_jobs_schema(db)
        db.commit()
        logger.info("Migration v2.38s: Ensured async screening jobs schema")
    except Exception as e:
        logger.error("Migration v2.38s failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38a: Portal periodic-review attestation fields. Keeps
    # periodic_reviews as the single review-state shell while adding a
    # client-facing attestation payload/status projection.
    try:
        _ensure_periodic_review_attestation_schema(db)
        db.commit()
        logger.info("Migration v2.38a: Ensured periodic review attestation schema")
    except Exception as e:
        logger.error("Migration v2.38a failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38b: compact periodic-review baseline metadata for
    # Application Overview without creating a separate review model.
    try:
        _ensure_periodic_review_baseline_schema(db)
        db.commit()
        logger.info("Migration v2.38b: Ensured periodic review baseline schema")
    except Exception as e:
        logger.error("Migration v2.38b failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38c: officer periodic-review workspace findings
    # draft fields for PRS-4.
    try:
        _ensure_periodic_review_findings_schema(db)
        db.commit()
        logger.info("Migration v2.38c: Ensured periodic review findings schema")
    except Exception as e:
        logger.error("Migration v2.38c failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38d: PRS-6 notification/reminder metadata on the
    # canonical periodic review shell.
    try:
        _ensure_periodic_review_notification_schema(db)
        db.commit()
        logger.info("Migration v2.38d: Ensured periodic review notification schema")
    except Exception as e:
        logger.error("Migration v2.38d failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.38e: PRS-7 risk reassessment and memo addendum
    # metadata on the canonical periodic review shell.
    try:
        _ensure_periodic_review_risk_reassessment_schema(db)
        db.commit()
        logger.info("Migration v2.38e: Ensured periodic review risk reassessment schema")
    except Exception as e:
        logger.error("Migration v2.38e failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.39: Store upload-time document hashes and index them for
    # duplicate detection. Existing unhashed rows are handled by a controlled
    # verification fallback until an operator backfill has populated hashes.
    try:
        _ensure_document_file_hash_schema(db)
        db.commit()
        logger.info("Migration v2.39: Ensured document file hash schema")
    except Exception as e:
        logger.error("Migration v2.39 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.40: Explicitly classify document evidence for pilot-proof
    # reporting without changing document verification or approval gates.
    try:
        _ensure_document_evidence_classification_schema(db)
        db.commit()
        logger.info("Migration v2.40: Ensured document evidence classification schema")
    except Exception as e:
        logger.error("Migration v2.40 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.41: Governed staging-only workflow-test evidence acceptance.
    #
    # This stores a separate workflow-only acceptance trail without changing
    # document verification truth or pilot approval-proof classification.
    try:
        _ensure_document_workflow_test_acceptance_schema(db)
        _ensure_application_enhanced_requirement_fulfilment_columns(db)
        db.commit()
        logger.info("Migration v2.41: Ensured workflow-test evidence acceptance schema")
    except Exception as e:
        logger.error("Migration v2.41 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.42: Officer upload attribution for canonical evidence rows.
    # Existing databases may already have document evidence columns without this
    # audit field, so keep it idempotent and independent from enforcement.
    try:
        _ensure_document_upload_audit_schema(db)
        db.commit()
        logger.info("Migration v2.42: Ensured document upload audit schema")
    except Exception as e:
        logger.error("Migration v2.42 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.43: Submit-to-Compliance handoff metadata
    # (PR-SUBMIT-TO-COMPLIANCE-WORKFLOW-1). Stores who submitted a case to senior
    # compliance review, why, the blocker snapshot, and whether the submission was
    # mandatory or discretionary. The authoritative state is the application status
    # ('submitted_to_compliance'); these columns are the projection backing data.
    try:
        _ensure_submit_to_compliance_columns(db)
        db.commit()
        logger.info("Migration v2.43: Ensured submit-to-compliance metadata schema")
    except Exception as e:
        logger.error("Migration v2.43 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.43a (PostgreSQL): allow 'submitted_to_compliance' in the
    # applications status CHECK constraint. SQLite picks this up from the CREATE
    # TABLE definition on fresh databases; Postgres needs the constraint rebuilt.
    if USE_POSTGRESQL:
        try:
            constraint_row = db.execute("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'applications_status_check'
                  AND conrelid = 'applications'::regclass
            """).fetchone()
            constraint_def = None
            if constraint_row:
                constraint_def = (
                    constraint_row.get("pg_get_constraintdef")
                    if isinstance(constraint_row, dict) else constraint_row[0]
                )
            if constraint_def and "'submitted_to_compliance'" in constraint_def:
                logger.info("Migration v2.43a: status CHECK already includes 'submitted_to_compliance'")
            else:
                db.execute("ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_status_check")
                db.execute("""ALTER TABLE applications ADD CONSTRAINT applications_status_check
                    CHECK(status IN ('draft','submitted','prescreening_submitted','pricing_review','pricing_accepted',
                    'pre_approval_review','pre_approved','kyc_documents','kyc_submitted','compliance_review',
                    'submitted_to_compliance','in_review','under_review',
                    'edd_required','approved','rejected','rmi_sent','withdrawn'))""")
                db.commit()
                logger.info("Migration v2.43a: Added 'submitted_to_compliance' to applications status CHECK")
        except Exception as e:
            logger.debug("Migration v2.43a status constraint update: %s", e)
            try:
                db.rollback()
            except Exception:
                pass

    # Migration v2.44: Change Request approval preconditions
    # (PR-CM-APPROVAL-PRECONDITIONS-1). Stores evidence-backed precondition
    # result markers (screening/risk) used to gate CM approval. Additive — no
    # change to existing rows or approval behaviour by itself.
    try:
        _ensure_change_request_precondition_schema(db)
        db.commit()
        logger.info("Migration v2.44: Ensured change request precondition schema")
    except Exception as e:
        logger.error("Migration v2.44 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.45: separate provider PEP detections from party PEP state.
    # Conservative data repair only: provider matches remain in screening
    # evidence/review queues; party ``is_pep`` is reset only where no client
    # declaration or officer confirmation exists.
    try:
        _repair_provider_detected_pep_party_flags_once(db)
        db.commit()
        logger.info("Migration v2.45: Ensured provider PEP detection separation repair")
    except Exception as e:
        logger.error("Migration v2.45 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.46 (PR-27 / audit-log-tamper-evidence-1): hash-chain columns
    # on the general audit_log, mirroring supervisor_audit_log's v2 chain.
    # Additive only — existing rows keep NULL previous_hash/entry_hash and are
    # treated as `legacy` by verify_audit_log_chain. Nothing is routed through
    # append_audit_log yet (that wiring is a separate, decision-gated step), so
    # this migration changes no write behaviour; it only makes the chain possible.
    try:
        added = False
        if not _safe_column_exists(db, "audit_log", "previous_hash"):
            logger.info("Migration v2.46: Adding audit_log.previous_hash")
            db.execute("ALTER TABLE audit_log ADD COLUMN previous_hash TEXT")
            added = True
        if not _safe_column_exists(db, "audit_log", "entry_hash"):
            logger.info("Migration v2.46: Adding audit_log.entry_hash")
            db.execute("ALTER TABLE audit_log ADD COLUMN entry_hash TEXT")
            added = True
        # Structural anti-fork backstop (mirrors uq_sup_audit_prev_hash): at most
        # one entry may reference a given predecessor hash, so two concurrent
        # appends can never both chain off the same tail. Genesis rows (NULL
        # previous_hash) and legacy rows are excluded by the partial predicate,
        # valid on PostgreSQL and SQLite >= 3.8. Best-effort: a pre-existing fork
        # logs loudly rather than blocking startup.
        try:
            db.executescript(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_log_prev_hash "
                "ON audit_log(previous_hash) WHERE previous_hash IS NOT NULL;"
            )
        except Exception as exc:
            logger.error(
                "Migration v2.46: FAILED to create the anti-fork unique index on "
                "audit_log.previous_hash — the structural fork backstop is NOT "
                "active and a pre-existing duplicate previous_hash (a chain fork) "
                "likely exists and needs manual investigation/repair: %s", exc,
            )
        try:
            db.executescript(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_entry_hash "
                "ON audit_log(entry_hash);"
            )
        except Exception as exc:
            logger.debug("Migration v2.46: entry_hash index create skipped: %s", exc)
        if added:
            db.commit()
            logger.info("Migration v2.46: audit_log hash-chain columns added")
        else:
            db.commit()
            logger.info("Migration v2.46: audit_log hash-chain columns already present")
    except Exception as e:
        logger.error("Migration v2.46 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.48 (P12-8 / DCI-021): regulator-reconstructable purge
    # evidence columns on data_purge_log. Additive only — all nullable TEXT,
    # old rows keep NULLs, old images ignore the columns (rollback-safe).
    try:
        _ensure_data_purge_log_evidence_columns(db)
        db.commit()
        logger.info("Migration v2.48: Ensured data_purge_log evidence columns")
    except Exception as e:
        logger.error("Migration v2.48 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.49 (P12-9 / DCI-028): request correlation id on audit_log.
    # Additive nullable TEXT — old rows keep NULL, old images ignore it, and
    # the hash chain (v2.46 / append_audit_log) computes from an explicit
    # field list, so the new column does not affect chain verification.
    # NOTE: v2.47/v2.48 are used by the P12-5 / P12-8 branches.
    try:
        if not _safe_column_exists(db, "audit_log", "request_id"):
            db.execute("ALTER TABLE audit_log ADD COLUMN request_id TEXT")
            db.commit()
            logger.info("Migration v2.49: audit_log.request_id added")
    except Exception as e:
        logger.error("Migration v2.49 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.50 (APP-727-001): immutable application scope on audit_log.
    # Additive nullable TEXT so legacy rows remain readable but application
    # detail activity can reject rows that only match reused refs or text.
    try:
        changed = False
        if not _safe_column_exists(db, "audit_log", "application_id"):
            db.execute("ALTER TABLE audit_log ADD COLUMN application_id TEXT")
            changed = True
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_application_id ON audit_log(application_id)")
        db.commit()
        if changed:
            logger.info("Migration v2.50: audit_log.application_id added")
    except Exception as e:
        logger.error("Migration v2.50 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.51 (BSA-002): shared DB-backed fail-closed rate limiter.
    # Creates a keyed, atomic fixed-window counter table for selected
    # low-frequency sensitive endpoints. Existing append-only rate_limits rows
    # remain untouched for legacy best-effort limiter callers.
    try:
        if db.is_postgres:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_rate_limits (
                    key TEXT PRIMARY KEY,
                    window_start DOUBLE PRECISION NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    expires_at DOUBLE PRECISION NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_shared_rate_limits_expires_at
                    ON shared_rate_limits(expires_at);
                """
            )
        else:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_rate_limits (
                    key TEXT PRIMARY KEY,
                    window_start REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_shared_rate_limits_expires_at
                    ON shared_rate_limits(expires_at);
                """
            )
        db.commit()
        logger.info("Migration v2.51: shared_rate_limits table ensured")
    except Exception as e:
        logger.error("Migration v2.51 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    # Migration v2.52 (BSA-003B): durable supervisor human-review evidence.
    # These tables intentionally use the shared DB and portable timestamps;
    # HumanReviewService no longer creates or writes container-local SQLite.
    try:
        # Reconcile legacy columns/types before the original v2.52 DDL reaches
        # any index statement.  The DDL below remains the executable inline
        # migration/fresh-schema contract and is safe after this preflight.
        _ensure_supervisor_human_review_persistence_schema(db)
        if db.is_postgres:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS supervisor_escalations (
                    id TEXT PRIMARY KEY,
                    pipeline_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    escalation_source TEXT NOT NULL,
                    source_id TEXT,
                    escalation_level TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    context_json TEXT DEFAULT '{}',
                    assigned_to TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    sla_deadline TIMESTAMP,
                    resolved_at TIMESTAMP,
                    escalated_by_id TEXT NOT NULL,
                    escalated_by_name TEXT NOT NULL,
                    escalated_by_role TEXT NOT NULL,
                    request_id TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_app ON supervisor_escalations(application_id);
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_pipeline ON supervisor_escalations(pipeline_id);
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_status_created ON supervisor_escalations(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_level_status ON supervisor_escalations(escalation_level, status);

                CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
                    id TEXT PRIMARY KEY,
                    pipeline_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    escalation_id TEXT REFERENCES supervisor_escalations(id),
                    review_type TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    reviewer_name TEXT NOT NULL,
                    reviewer_role TEXT NOT NULL,
                    ai_recommendation TEXT,
                    ai_confidence REAL,
                    ai_risk_level TEXT,
                    rules_recommendation TEXT,
                    rules_triggered TEXT DEFAULT '[]',
                    contradictions_json TEXT DEFAULT '[]',
                    decision TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    risk_level_assigned TEXT,
                    conditions TEXT,
                    follow_up_required INTEGER NOT NULL DEFAULT 0,
                    follow_up_details TEXT,
                    is_ai_override INTEGER NOT NULL DEFAULT 0,
                    override_reason TEXT,
                    review_started_at TIMESTAMP,
                    decision_at TIMESTAMP NOT NULL,
                    request_id TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_app ON supervisor_human_reviews(application_id);
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_pipeline ON supervisor_human_reviews(pipeline_id);
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_reviewer ON supervisor_human_reviews(reviewer_id);
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_decision_at ON supervisor_human_reviews(decision_at);

                CREATE TABLE IF NOT EXISTS supervisor_overrides (
                    id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL REFERENCES supervisor_human_reviews(id),
                    application_id TEXT NOT NULL,
                    agent_type TEXT,
                    override_type TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    override_value TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    officer_id TEXT NOT NULL,
                    officer_name TEXT NOT NULL,
                    officer_role TEXT NOT NULL,
                    approver_id TEXT,
                    approver_name TEXT,
                    approved_at TIMESTAMP,
                    request_id TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sup_overrides_app ON supervisor_overrides(application_id);
                CREATE INDEX IF NOT EXISTS idx_sup_overrides_review ON supervisor_overrides(review_id);
                CREATE INDEX IF NOT EXISTS idx_sup_overrides_created ON supervisor_overrides(created_at);
                """
            )
        else:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS supervisor_escalations (
                    id TEXT PRIMARY KEY,
                    pipeline_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    escalation_source TEXT NOT NULL,
                    source_id TEXT,
                    escalation_level TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    context_json TEXT DEFAULT '{}',
                    assigned_to TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    sla_deadline TEXT,
                    resolved_at TEXT,
                    escalated_by_id TEXT NOT NULL,
                    escalated_by_name TEXT NOT NULL,
                    escalated_by_role TEXT NOT NULL,
                    request_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_app ON supervisor_escalations(application_id);
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_pipeline ON supervisor_escalations(pipeline_id);
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_status_created ON supervisor_escalations(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_sup_escalations_level_status ON supervisor_escalations(escalation_level, status);

                CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
                    id TEXT PRIMARY KEY,
                    pipeline_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    escalation_id TEXT REFERENCES supervisor_escalations(id),
                    review_type TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    reviewer_name TEXT NOT NULL,
                    reviewer_role TEXT NOT NULL,
                    ai_recommendation TEXT,
                    ai_confidence REAL,
                    ai_risk_level TEXT,
                    rules_recommendation TEXT,
                    rules_triggered TEXT DEFAULT '[]',
                    contradictions_json TEXT DEFAULT '[]',
                    decision TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    risk_level_assigned TEXT,
                    conditions TEXT,
                    follow_up_required INTEGER NOT NULL DEFAULT 0,
                    follow_up_details TEXT,
                    is_ai_override INTEGER NOT NULL DEFAULT 0,
                    override_reason TEXT,
                    review_started_at TEXT,
                    decision_at TEXT NOT NULL,
                    request_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_app ON supervisor_human_reviews(application_id);
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_pipeline ON supervisor_human_reviews(pipeline_id);
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_reviewer ON supervisor_human_reviews(reviewer_id);
                CREATE INDEX IF NOT EXISTS idx_sup_reviews_decision_at ON supervisor_human_reviews(decision_at);

                CREATE TABLE IF NOT EXISTS supervisor_overrides (
                    id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL REFERENCES supervisor_human_reviews(id),
                    application_id TEXT NOT NULL,
                    agent_type TEXT,
                    override_type TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    override_value TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    officer_id TEXT NOT NULL,
                    officer_name TEXT NOT NULL,
                    officer_role TEXT NOT NULL,
                    approver_id TEXT,
                    approver_name TEXT,
                    approved_at TEXT,
                    request_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sup_overrides_app ON supervisor_overrides(application_id);
                CREATE INDEX IF NOT EXISTS idx_sup_overrides_review ON supervisor_overrides(review_id);
                CREATE INDEX IF NOT EXISTS idx_sup_overrides_created ON supervisor_overrides(created_at);
                """
            )
        db.commit()
        logger.info("Migration v2.52: durable supervisor review tables ensured")
    except Exception as e:
        logger.error("Migration v2.52 failed: %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise

    # Migration v2.47 (P12-5 / DCI-006) runs from init_db AFTER
    # _ensure_supervisor_audit_log_schema so a legacy-audit-schema database
    # gets its rebuild first and the enum constraints in the same boot.


def _ensure_data_purge_log_evidence_columns(db: 'DBConnection'):
    """P12-8 / DCI-021: additive purge-evidence columns for long-lived DBs.

    Fresh schemas carry these in the CREATE TABLE DDL; existing databases
    gain them here (all nullable TEXT — purely additive, old rows keep
    NULLs, old images ignore the columns, so image rollback stays safe).
    """
    if not _safe_table_exists(db, "data_purge_log"):
        return
    for col in ("subject_id", "application_id", "tables_affected",
                "per_table_counts", "purge_batch_id", "evidence_json"):
        if not _safe_column_exists(db, "data_purge_log", col):
            db.execute(f"ALTER TABLE data_purge_log ADD COLUMN {col} TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_purge_log_batch "
        "ON data_purge_log(purge_batch_id)"
    )
    # Drop the legacy purged_by -> users(id) FK on long-lived PostgreSQL
    # databases: the scheduler's actor string ('system-scheduler') is not a
    # users row, so the FK made every real scheduled purge fail its evidence
    # INSERT and roll back (adversarial review, P12-8). SQLite never enforced
    # it (no PRAGMA foreign_keys), so only PG needs the drop.
    if db.is_postgres:
        fks = db.execute(
            """
            SELECT c.conname
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
             WHERE t.relname = 'data_purge_log'
               AND c.contype = 'f'
               AND EXISTS (
                    SELECT 1 FROM pg_attribute a
                     WHERE a.attrelid = c.conrelid
                       AND a.attnum = ANY(c.conkey)
                       AND a.attname = 'purged_by'
               )
            """
        ).fetchall()
        for fk in fks:
            name = dict(fk).get("conname")
            if name:
                db.execute(
                    f"ALTER TABLE data_purge_log DROP CONSTRAINT IF EXISTS {_pg_quote_identifier(name)}"
                )
                logger.info(
                    "Migration v2.48: dropped legacy data_purge_log.purged_by "
                    "FK %s (scheduler attribution strings are not users rows)",
                    name,
                )


# P12-5 / DCI-006 constraint-repair specs:
# (table, column, allowed_values, null_backfill, set_not_null)
#
# Backfill policy decisions (adversarial review, P12-5):
#   * clients.status NULL/blank -> 'inactive', NOT 'active': a NULL-status
#     client cannot authenticate today (login filters on status='active'),
#     so promoting the anomaly to 'active' would silently RE-ENABLE access.
#     'inactive' is fail-closed — an operator reviews and re-enables.
#   * supervisor_audit_log.severity is NOT backfilled here: severity is part
#     of the v2 entry-hash payload on the hash-chained audit table, and this
#     helper's contract is that evidence rows are never rewritten.  (The
#     legacy schema-repair path owns its own pre-existing severity
#     normalisation.)  NULL severity rows are CHECK-legal and simply keep
#     their NULL.
STATUS_ENUM_CONSTRAINT_SPECS = (
    ("clients", "status", CLIENT_STATUS_VALUES, "inactive", True),
    ("agent_executions", "status", AGENT_EXECUTION_STATUS_VALUES, None, False),
    ("agent_executions", "source", AGENT_EXECUTION_SOURCE_VALUES, "ai", False),
    ("supervisor_pipeline_results", "status", SUPERVISOR_PIPELINE_STATUS_VALUES, None, False),
    ("supervisor_audit_log", "event_type", SUPERVISOR_AUDIT_EVENT_TYPE_VALUES, None, False),
    ("supervisor_audit_log", "severity", SUPERVISOR_AUDIT_SEVERITY_VALUES, None, False),
    ("compliance_memos", "supervisor_status", COMPLIANCE_MEMO_SUPERVISOR_STATUS_VALUES, "pending", False),
    ("compliance_memos", "rule_engine_status", COMPLIANCE_MEMO_RULE_ENGINE_STATUS_VALUES, "pending", False),
)


def _ensure_status_enum_constraints(db: 'DBConnection'):
    """Migration v2.47 (P12-5 / DCI-006): workflow status/source enum repair.

    Fresh installs get identical CHECKs from the CREATE TABLE DDL in both
    schemas; this helper repairs LONG-LIVED databases every boot:

    1. NULL/blank backfill (portable UPDATE, both engines) where the column
       is semantically non-null and has a DDL default.
    2. Off-canon detection: rows carrying values outside the canon are NEVER
       rewritten (agent_executions / supervisor_audit_log rows are evidence;
       supervisor_audit_log is additionally hash-chained) — instead the
       constraint for that column is SKIPPED with a loud ERROR listing the
       offending values, and retried on the next boot after operator
       remediation.
    3. Constraint installation is PostgreSQL-only (SQLite cannot
       ALTER TABLE ... ADD CONSTRAINT; dev/test SQLite databases are
       recreated freshly and covered by the DDL CHECKs).  Stale/conflicting
       historical CHECKs on the column are dropped and replaced.

    clients.status additionally gains NOT NULL (mirrors users.status).
    """
    for _tbl, _col, _allowed, _null_fill, _set_not_null in STATUS_ENUM_CONSTRAINT_SPECS:
        try:
            if not _safe_column_exists(db, _tbl, _col):
                continue
            qt = _pg_quote_identifier(_tbl) if db.is_postgres else _tbl
            qc = _pg_quote_identifier(_col) if db.is_postgres else _col

            existing_constraints = []
            if db.is_postgres:
                # Steady-state short-circuit (adversarial review M2): if the
                # canonical constraint is already installed with exactly the
                # canon values (and NOT NULL where required), skip the
                # backfill UPDATE, the off-canon scan, and the DROP+ADD —
                # otherwise every boot would take ACCESS EXCLUSIVE locks and
                # full validation scans on unbounded tables
                # (supervisor_audit_log is append-only and never purged).
                existing_constraints = _postgres_check_constraints_for_column(db, _tbl, _col)
                canonical = [
                    c for c in existing_constraints
                    if c.get("conname") == f"{_tbl}_{_col}_check"
                    and set(re.findall(r"'([^']*)'", c.get("definition") or "")) == set(_allowed)
                ]
                if canonical and len(existing_constraints) == 1:
                    if _set_not_null:
                        nullable = db.execute(
                            "SELECT is_nullable FROM information_schema.columns "
                            "WHERE table_name = ? AND column_name = ?",
                            (_tbl, _col),
                        ).fetchone()
                        if dict(nullable or {}).get("is_nullable") == "NO":
                            continue
                    else:
                        continue

            backfilled = 0
            if _null_fill is not None:
                db.execute(
                    f"UPDATE {qt} SET {qc} = ? WHERE {qc} IS NULL OR TRIM({qc}) = ''",
                    (_null_fill,),
                )
                backfilled = getattr(getattr(db, "_cursor", None), "rowcount", 0) or 0
                if backfilled:
                    logger.warning(
                        "Migration v2.47: backfilled %d NULL/blank %s.%s row(s) "
                        "to %r (P12-5 / DCI-006)",
                        backfilled, _tbl, _col, _null_fill,
                    )
            placeholders = ", ".join("?" for _ in _allowed)
            bad = db.execute(
                f"SELECT {qc} AS v, COUNT(*) AS c FROM {qt} "
                f"WHERE {qc} IS NOT NULL AND {qc} NOT IN ({placeholders}) "
                f"GROUP BY {qc} LIMIT 20",
                tuple(_allowed),
            ).fetchall()
            if bad:
                offenders = {str(r["v"]): int(r["c"]) for r in (dict(x) for x in bad)}
                if db.is_postgres:
                    # Surface any conflicting historical CHECK still installed
                    # on the column (adversarial review m5): a stale
                    # wrong-vocabulary constraint keeps REJECTING modern
                    # writes while the off-canon rows block its replacement —
                    # the operator needs both facts to break the deadlock.
                    stale = {
                        c.get("conname"): c.get("definition")
                        for c in existing_constraints
                    }
                    logger.error(
                        "Migration v2.47: %s.%s holds OFF-CANON values %s — rows "
                        "preserved (never rewritten), CHECK constraint SKIPPED for "
                        "this column; remediate the data and the constraint will "
                        "install on the next boot. Existing CHECK constraint(s) "
                        "still installed on the column: %s (P12-5 / DCI-006)",
                        _tbl, _col, offenders, stale or "none",
                    )
                else:
                    logger.error(
                        "Migration v2.47: %s.%s holds OFF-CANON values %s — rows "
                        "preserved. NOTE: SQLite cannot retrofit CHECK "
                        "constraints; fresh schemas enforce via DDL, so "
                        "remediate this data or recreate the dev database "
                        "(P12-5 / DCI-006)",
                        _tbl, _col, offenders,
                    )
                db.commit()
                continue
            if db.is_postgres:
                _replace_postgres_column_check_constraint(
                    db,
                    table=_tbl,
                    column=_col,
                    constraint_name=f"{_tbl}_{_col}_check",
                    allowed_values=_allowed,
                )
                if _set_not_null:
                    db.execute(f"ALTER TABLE {qt} ALTER COLUMN {qc} SET NOT NULL")
                db.commit()
                logger.info(
                    "Migration v2.47: %s.%s enum constraint installed (%d values)",
                    _tbl, _col, len(_allowed),
                )
            else:
                db.commit()
        except Exception as e:
            logger.error(
                "Migration v2.47 failed for %s.%s: %s", _tbl, _col, e, exc_info=True
            )
            try:
                db.rollback()
            except Exception:
                pass


def _ensure_change_request_precondition_schema(db: 'DBConnection'):
    """Add CM approval-precondition result storage (PR-CM-APPROVAL-PRECONDITIONS-1).

    Additive: a single JSON column on change_requests holding evidence-backed
    screening/risk precondition results. No behaviour change from the column
    itself; the approval gate that reads it lives in change_management.py.
    """
    if not _safe_table_exists(db, "change_requests"):
        return
    if not _safe_column_exists(db, "change_requests", "precondition_results"):
        db.execute("ALTER TABLE change_requests ADD COLUMN precondition_results TEXT")


def _ensure_submit_to_compliance_columns(db: 'DBConnection'):
    """Add Submit-to-Compliance handoff metadata columns to applications.

    Idempotent and additive: existing rows keep NULLs. No existing column,
    verification, or approval-gate behaviour is changed.
    """
    if not _safe_table_exists(db, "applications"):
        return
    column_types = {
        "submitted_to_compliance_at": "TIMESTAMP" if db.is_postgres else "TEXT",
        "submitted_to_compliance_by": "TEXT REFERENCES users(id)",
        "submission_note": "TEXT",
        "submission_basis": "TEXT",            # JSON array of basis tags
        "submission_kind": "TEXT",             # 'mandatory' | 'discretionary'
        "submission_blocker_snapshot": "TEXT",  # JSON snapshot of gate blockers
    }
    for column, definition in column_types.items():
        if not _safe_column_exists(db, "applications", column):
            db.execute(
                f"ALTER TABLE applications ADD COLUMN {column} {definition}"
            )


def _repair_risk_config_shapes(db: 'DBConnection'):
    """Migration v2.16: Repair malformed risk_config scoring columns.

    Detects and fixes the known corruption pattern where score-mapping columns
    (country_risk_scores, sector_risk_scores, entity_type_scores) were stored as
    lists-of-dicts instead of flat dicts.  Also re-seeds from defaults if columns
    are empty/null.
    """
    row = db.execute(
        "SELECT country_risk_scores, sector_risk_scores, entity_type_scores "
        "FROM risk_config WHERE id=1"
    ).fetchone()
    if not row:
        return  # No risk_config row — will be seeded separately

    needs_update = False
    repaired = {}

    # DCI-008: in fail-closed environments a malformed column must NOT be
    # silently reset to '{}' — that converts a hard load failure (503 until an
    # operator fixes the config) into a silent hardcoded-defaults fallback on
    # the next container restart, defeating the fail-closed gate exactly where
    # it matters. The lossless list-of-dicts normalization still runs
    # everywhere; only the destructive reset-to-empty is suppressed.
    from environment import get_environment
    _fail_closed = get_environment() in ("staging", "production")

    for col in ("country_risk_scores", "sector_risk_scores", "entity_type_scores"):
        raw = row[col]
        if not raw or raw == '{}':
            continue  # Empty — will be filled by _populate_default_scoring_config

        # Parse the stored JSON
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            if _fail_closed:
                logger.error(
                    "Migration v2.16: %s is unparsable — leaving in place for the "
                    "fail-closed risk-config gate (operator must fix the config)", col)
                continue
            logger.warning("Migration v2.16: %s is unparsable, will re-seed", col)
            repaired[col] = '{}'
            needs_update = True
            continue

        if isinstance(parsed, dict):
            continue  # Already correct shape

        # Attempt normalization: list-of-dicts → flat dict
        if isinstance(parsed, list):
            merged = {}
            for item in parsed:
                if isinstance(item, dict):
                    merged.update(item)
            if merged:
                logger.info(
                    "Migration v2.16: repaired %s from list-of-dicts to dict (%d entries)",
                    col, len(merged),
                )
                repaired[col] = json.dumps(merged)
                needs_update = True
            else:
                if _fail_closed:
                    logger.error(
                        "Migration v2.16: %s is malformed list — leaving in place for "
                        "the fail-closed risk-config gate", col)
                    continue
                logger.warning(
                    "Migration v2.16: %s is malformed list (not list-of-dicts), resetting to empty",
                    col,
                )
                repaired[col] = '{}'
                needs_update = True
        else:
            if _fail_closed:
                logger.error(
                    "Migration v2.16: %s has unexpected type %s — leaving in place "
                    "for the fail-closed risk-config gate", col, type(parsed).__name__)
                continue
            logger.warning(
                "Migration v2.16: %s has unexpected type %s, resetting to empty",
                col, type(parsed).__name__,
            )
            repaired[col] = '{}'
            needs_update = True

    if needs_update:
        # Build SET clause for only the columns that need repair
        set_parts = []
        params = []
        for col, val in repaired.items():
            set_parts.append(f"{col}=?")
            params.append(val)
        if set_parts:
            sql = f"UPDATE risk_config SET {', '.join(set_parts)} WHERE id=1"
            db.execute(sql, params)
            db.commit()
            logger.info("Migration v2.16: risk_config scoring columns repaired")
    else:
        logger.debug("Migration v2.16: risk_config scoring columns are already well-formed")


def _populate_default_scoring_config(db: 'DBConnection'):
    """Populate default country/sector/entity scores for existing risk_config rows."""
    existing = db.execute(
        "SELECT country_risk_scores, sector_risk_scores, entity_type_scores "
        "FROM risk_config WHERE id=1"
    ).fetchone()
    default_country_scores = {
        "australia": 1, "canada": 1, "france": 1, "germany": 1, "hong kong": 1,
        "ireland": 1, "japan": 1, "luxembourg": 1, "netherlands": 1, "new zealand": 1,
        "singapore": 1, "switzerland": 1, "united kingdom": 1, "united states": 1,
        "austria": 1, "belgium": 1, "denmark": 1, "finland": 1, "norway": 1,
        "sweden": 1, "south korea": 1, "israel": 1, "iceland": 1, "italy": 1,
        "portugal": 1, "spain": 1, "taiwan": 1, "uk": 1, "usa": 1,
        "bahrain": 2, "botswana": 2, "brazil": 2, "chile": 2, "china": 2,
        "india": 2, "indonesia": 2, "kuwait": 2, "malaysia": 2, "mauritius": 2,
        "mexico": 2, "morocco": 2, "oman": 2, "qatar": 2, "rwanda": 2,
        "saudi arabia": 2, "turkey": 2, "uae": 2,
        "uganda": 2, "ghana": 2, "ivory coast": 2, "jordan": 2, "sri lanka": 2, "tunisia": 2,
        "jersey": 2, "guernsey": 2, "isle of man": 2, "liechtenstein": 2,
        "estonia": 2, "pakistan": 2, "seychelles": 2,
        "algeria": 3, "burkina faso": 3, "cameroon": 3, "democratic republic of congo": 3,
        "haiti": 3, "kenya": 3, "laos": 3, "lebanon": 3, "mali": 3, "monaco": 3,
        "mozambique": 3, "nigeria": 3, "philippines": 3, "senegal": 3, "south africa": 3,
        "south sudan": 3, "tanzania": 3, "venezuela": 3, "vietnam": 3, "yemen": 3,
        "bermuda": 3, "vanuatu": 3, "samoa": 3, "marshall islands": 3, "iraq": 3,
        "iran": 4, "north korea": 4, "myanmar": 4, "russia": 4, "syria": 4, "belarus": 4,
        "cuba": 4, "crimea": 4, "afghanistan": 4, "somalia": 4, "libya": 4, "eritrea": 4, "sudan": 4,
        "bvi": 4, "british virgin islands": 4, "cayman islands": 4, "panama": 4
    }
    default_sector_scores = {
        "regulated financial": 1, "government": 1, "bank": 1, "listed company": 1,
        "agriculture": 1, "education": 1,
        "healthcare": 2, "technology": 2, "software": 2, "saas": 2, "manufacturing": 2,
        "retail": 2, "e-commerce": 2, "media": 2, "logistics": 2, "insurance": 2,
        "telecommunications": 2, "banking": 2,
        "construction": 3, "import": 3, "export": 3, "real estate": 3, "mining": 3,
        "oil": 3, "gas": 3, "energy": 3, "money services": 3, "forex": 3, "precious": 3,
        "non-profit": 3, "ngo": 3, "charity": 3, "advisory": 3,
        "management consulting": 3, "consulting": 3, "financial / tax advisory": 3,
        "fintech": 3, "e-money": 3, "legal": 3, "accounting": 3, "shipping": 3, "maritime": 3,
        "crypto": 4, "virtual asset": 4, "gambling": 4, "gaming": 4, "betting": 4,
        "arms": 4, "defence": 4, "military": 4, "shell company": 4, "nominee": 4,
        "precious metals": 4
    }
    default_entity_scores = {
        "listed company": 1, "regulated financial institution": 1, "regulated fi": 1,
        "regulated entity": 1, "government": 1, "government body": 1, "public sector": 1,
        "listed": 1, "regulated": 1,
        "large private company": 2, "large private": 2, "sme": 2, "private company": 2,
        "regulated fund": 2,
        "newly incorporated": 3, "trust": 3, "foundation": 3, "ngo": 3, "non-profit": 3,
        "unregulated fund": 4, "spv": 4, "shell company": 4, "shell": 4
    }

    def _merge_missing_defaults(raw, defaults):
        try:
            parsed = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        merged = dict(parsed)
        changed = not parsed
        for key, score in defaults.items():
            if key not in merged:
                merged[key] = score
                changed = True
        return merged, changed

    if existing:
        country_scores, country_changed = _merge_missing_defaults(existing["country_risk_scores"], default_country_scores)
        sector_scores, sector_changed = _merge_missing_defaults(existing["sector_risk_scores"], default_sector_scores)
        entity_scores, entity_changed = _merge_missing_defaults(existing["entity_type_scores"], default_entity_scores)
        if country_changed or sector_changed or entity_changed:
            db.execute(
                "UPDATE risk_config SET country_risk_scores=?, sector_risk_scores=?, entity_type_scores=? WHERE id=1",
                (
                    json.dumps(country_scores, sort_keys=True),
                    json.dumps(sector_scores, sort_keys=True),
                    json.dumps(entity_scores, sort_keys=True),
                )
            )
        return

    db.execute(
        "UPDATE risk_config SET country_risk_scores=?, sector_risk_scores=?, entity_type_scores=? WHERE id=1",
        (
            json.dumps(default_country_scores, sort_keys=True),
            json.dumps(default_sector_scores, sort_keys=True),
            json.dumps(default_entity_scores, sort_keys=True),
        )
    )


def _apply_pr_cr1r_manual_score_defaults_once(db: 'DBConnection') -> bool:
    """One-time PR-CR1R data repair for incomplete manual scoring maps.

    PR-CR1R restored `risk_config.country_risk_scores` as the active country
    risk source. Some long-lived environments already had partial manual maps
    persisted after the snapshot UI was removed. This repair adds missing
    default manual keys once, then records a data_migration_markers marker so
    future deliberate manual removals are not silently undone at every startup.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS data_migration_markers (
            marker_key TEXT PRIMARY KEY,
            description TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_marker = db.execute(
        "SELECT marker_key FROM data_migration_markers WHERE marker_key=?",
        (_PR_CR1R_MANUAL_DEFAULTS_MARKER_KEY,),
    ).fetchone()
    if existing_marker:
        return False

    _populate_default_scoring_config(db)
    marker_values = (
        "Backfill missing default manual country/sector/entity score keys after PR-CR1R",
    )
    if getattr(db, "is_postgres", False):
        db.execute(
            """
            INSERT INTO data_migration_markers (marker_key, description)
            VALUES (?, ?)
            ON CONFLICT (marker_key) DO NOTHING
            """,
            (_PR_CR1R_MANUAL_DEFAULTS_MARKER_KEY, *marker_values),
        )
    else:
        db.execute(
            "INSERT OR IGNORE INTO data_migration_markers (marker_key, description) VALUES (?, ?)",
            (_PR_CR1R_MANUAL_DEFAULTS_MARKER_KEY, *marker_values),
        )
    logger.info("PR-CR1R manual score defaults repair marked as applied")
    return True


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return None


def _normalized_subject_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _screening_item_has_provider_pep(item: Mapping[str, Any]) -> bool:
    if not isinstance(item, Mapping):
        return False
    if (
        item.get("undeclared_pep")
        or item.get("provider_detected_pep")
        or item.get("has_pep_hit")
    ):
        return True
    screening = item.get("screening") if isinstance(item.get("screening"), Mapping) else {}
    for result in screening.get("results") or []:
        if isinstance(result, Mapping) and result.get("is_pep"):
            return True
    return False


def _screening_item_matches_party(item: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    row_name = _normalized_subject_text(row.get("full_name"))
    row_keys = {
        _normalized_subject_text(row.get(key))
        for key in ("id", "person_key")
        if row.get(key)
    }
    item_names = {
        _normalized_subject_text(item.get(key))
        for key in ("name", "full_name", "subject_name", "person_name")
        if item.get(key)
    }
    item_keys = {
        _normalized_subject_text(item.get(key))
        for key in ("person_key", "personKey", "subject_key", "subject_id", "person_id", "source_id")
        if item.get(key)
    }
    return bool((row_keys and row_keys.intersection(item_keys)) or (row_name and row_name in item_names))


def _party_declaration_is_declared_or_confirmed(pep_declaration: Mapping[str, Any]) -> bool:
    status = str(pep_declaration.get("pep_status") or "").strip().lower()
    declared = _optional_bool(
        pep_declaration.get("client_declared_pep", pep_declaration.get("declared_pep"))
    )
    officer_verified = _optional_bool(
        pep_declaration.get("officer_verified_pep", pep_declaration.get("verified_pep"))
    )
    return (
        declared is True
        or officer_verified is True
        or status in {"declared_yes", "confirmed_pep"}
    )


def _repair_provider_detected_pep_party_table(
    db: 'DBConnection',
    *,
    table_name: str,
    screening_bucket: str,
) -> int:
    rows = db.execute(
        f"""
        SELECT p.id, p.application_id, p.person_key, p.full_name, p.is_pep,
               p.pep_declaration, a.prescreening_data
        FROM {table_name} p
        JOIN applications a ON a.id = p.application_id
        WHERE LOWER(CAST(p.is_pep AS TEXT)) IN ('yes','true','1','t','y')
        """
    ).fetchall()

    repaired = 0
    for raw_row in rows or []:
        row = dict(raw_row) if hasattr(raw_row, "keys") else {}
        pep_declaration = _json_object(row.get("pep_declaration"))
        if _party_declaration_is_declared_or_confirmed(pep_declaration):
            continue

        prescreening = _json_object(row.get("prescreening_data"))
        report = prescreening.get("screening_report")
        if not isinstance(report, dict):
            continue
        matching_provider_pep = any(
            _screening_item_has_provider_pep(item)
            and _screening_item_matches_party(item, row)
            for item in report.get(screening_bucket) or []
            if isinstance(item, Mapping)
        )
        if not matching_provider_pep:
            continue

        status = str(pep_declaration.get("pep_status") or "").strip().lower()
        pep_declaration.setdefault("declared_pep", False)
        pep_declaration.setdefault("client_declared_pep", False)
        if status in {"", "not_verified"}:
            pep_declaration["pep_status"] = "declared_no"
            pep_declaration.setdefault("pep_verification_source", "client_declaration")
        pep_declaration["provider_detection_repair"] = {
            "source": "PR-PEP-PROVIDER-DETECTION-SEPARATION-1",
            "reason": "provider_pep_detection_is_screening_evidence_not_party_pep_state",
        }

        db.execute(
            f"UPDATE {table_name} SET is_pep=?, pep_declaration=? WHERE id=?",
            (
                False if getattr(db, "is_postgres", False) else "No",
                json.dumps(pep_declaration, sort_keys=True),
                row.get("id"),
            ),
        )
        repaired += 1
    return repaired


def _repair_provider_detected_pep_party_flags_once(db: 'DBConnection') -> bool:
    """Undo legacy provider-PEP writeback without clearing real PEP states.

    Previous code copied unresolved provider PEP matches into directors/ubos
    ``is_pep``. This one-time repair only resets rows whose declaration does
    not show client declaration or officer confirmation and whose application
    still has a matching provider PEP screening item.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS data_migration_markers (
            marker_key TEXT PRIMARY KEY,
            description TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_marker = db.execute(
        "SELECT marker_key FROM data_migration_markers WHERE marker_key=?",
        (_PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY,),
    ).fetchone()
    if existing_marker:
        return False
    if not (
        _safe_table_exists(db, "applications")
        and _safe_table_exists(db, "directors")
        and _safe_table_exists(db, "ubos")
        and _safe_column_exists(db, "directors", "pep_declaration")
        and _safe_column_exists(db, "ubos", "pep_declaration")
    ):
        return False

    repaired = 0
    repaired += _repair_provider_detected_pep_party_table(
        db, table_name="directors", screening_bucket="director_screenings"
    )
    repaired += _repair_provider_detected_pep_party_table(
        db, table_name="ubos", screening_bucket="ubo_screenings"
    )
    description = (
        "Reset legacy party is_pep values that were derived only from unresolved provider PEP screening; "
        f"rows_repaired={repaired}"
    )
    if getattr(db, "is_postgres", False):
        db.execute(
            """
            INSERT INTO data_migration_markers (marker_key, description)
            VALUES (?, ?)
            ON CONFLICT (marker_key) DO NOTHING
            """,
            (_PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY, description),
        )
    else:
        db.execute(
            "INSERT OR IGNORE INTO data_migration_markers (marker_key, description) VALUES (?, ?)",
            (_PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY, description),
        )
    logger.info("PEP provider detection separation repair marked as applied; rows_repaired=%s", repaired)
    return True


# ============================================================================
# Seed Data
# ============================================================================

# Metadata used by _migrate_agent_definitions INSERT fallback when rows are missing
# Format: (name, icon, stage, supervisor_agent_type, risk_dimensions)
_AGENT_METADATA = {
    1:  ("Identity & Document Integrity Agent", "🔍", "Onboarding", "identity_document_integrity", ["D1"]),
    2:  ("External Database Cross-Verification Agent", "🔎", "Onboarding", "external_database_verification", ["D1", "D2"]),
    3:  ("FinCrime Screening Interpretation Agent", "💼", "Onboarding", "fincrime_screening", ["D1"]),
    4:  ("Corporate Structure & UBO Mapping Agent", "🏗️", "Onboarding", "corporate_structure_ubo", ["D1"]),
    5:  ("Compliance Memo & Risk Recommendation Agent", "📝", "Onboarding", "compliance_memo_risk", ["D1", "D2", "D3", "D4", "D5"]),
    6:  ("Periodic Review Preparation Agent", "📅", "Monitoring", "periodic_review_preparation", ["D1"]),
    7:  ("Adverse Media & PEP Monitoring Agent", "📡", "Monitoring", "adverse_media_pep_monitoring", ["D1"]),
    8:  ("Behaviour & Risk Drift Agent", "📈", "Monitoring", "behaviour_risk_drift", ["D1", "D5"]),
    9:  ("Regulatory Impact Agent", "⚖️", "Monitoring", "regulatory_impact", ["D2", "D3"]),
    10: ("Ongoing Compliance Review Agent", "📋", "Monitoring", "ongoing_compliance_review", ["D1", "D2", "D3", "D4", "D5"]),
}

_AGENT_DEFINITIONS_V2 = {
    1: {
        "description": (
            "Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured in Document Verification Policies. "
            "It can verify, flag, block reliance, recommend officer action, and trigger required follow-up. "
            "It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening."
        ),
        "checks": [
            "Configured document verification checks",
            "Upload gate checks",
            "Material issue detection",
            "Requested EDD evidence routing",
            "Manual-review-only evidence routing",
            "Workflow blocker mapping",
            "Follow-up requirement markers",
            "Unknown document review routing",
            "Audit/export detail preservation",
        ],
    },
    3: {
        "description": (
            "Policy-bounded screening interpreter. Reads stored screening results from prescreening_data. "
            "4 rule-based checks (retrieval, disambiguation), 4 hybrid (FP reduction, severity ranking, disposition), "
            "3 AI (adverse media assessment, narrative). Degraded mode when no screening report available."
        ),
        "checks": [
            "Sanctions hit retrieval (rule)",
            "PEP hit retrieval (rule)",
            "Adverse media hit retrieval (rule)",
            "Exact identity disambiguation (rule)",
            "Near-match identity disambiguation (hybrid)",
            "False-positive reduction (hybrid)",
            "Severity ranking of confirmed hits (hybrid)",
            "Adverse media relevance assessment (ai)",
            "Adverse media materiality / seriousness (ai)",
            "Consolidated screening narrative (ai)",
            "Recommended screening disposition (hybrid)",
        ],
    },
    2: {
        "description": (
            "Rule-based registry verification with provider abstraction. Checks company identity data against external registries "
            "(OpenCorporates, Companies House, CBRD, ADGM, DIFC). Runs in degraded mode when no external API credentials are configured."
        ),
        "checks": [
            "Registry source selection (rule)",
            "Company registration number lookup (rule)",
            "Entity name match to registry (rule)",
            "Incorporation date match (rule)",
            "Company status check (rule)",
            "Jurisdiction match (rule)",
            "Company type / legal form (rule)",
            "Registered address match (hybrid)",
            "Director names cross-check (hybrid)",
            "Shareholder names cross-check (hybrid)",
            "UBO declarations vs registry shareholders (hybrid)",
            "Registry filing recency / availability (rule)",
            "Interpretation of unusual registry output (hybrid)",
        ],
    },
    4: {
        "description": (
            "Rule-based ownership mapping with indirect path tracking, circular ownership detection, "
            "nominee/trust/holding detection, and complexity scoring. All checks are deterministic — no AI calls."
        ),
        "checks": [
            "Direct ownership calculation (rule)", "Indirect ownership via intermediaries (rule)",
            "UBO threshold qualification ≥25% (rule)", "Total ownership completeness (rule)",
            "Circular ownership detection (rule)", "Nominee arrangement detection (rule)",
            "Trust/foundation structure detection (rule)", "Holding company/SPV detection (rule)",
            "Opaque jurisdiction flagging (rule)", "Shell company indicator aggregation (rule)",
            "Complexity scoring (rule)", "Ownership arithmetic validation (rule)",
            "Escalation logic (rule)",
        ],
    },
    5: {
        "description": (
            "Unified compliance memo agent. Bridges to authoritative memo path enforcing Rules 4A-4E, "
            "computing 7 risk dimensions, and generating an 11-section memo. Classification-tagged output "
            "(rule/hybrid/ai). Includes risk-model divergence cross-check."
        ),
        "checks": [
            "Document completeness score (rule)", "Jurisdiction risk score (rule)",
            "Industry/sector risk score (rule)", "Product/service risk score (rule)",
            "Channel/delivery risk score (rule)", "Ownership complexity ingestion (rule)",
            "Screening severity ingestion (rule)", "Weighted total risk score (rule)",
            "Risk tier bucket (rule)", "Mandatory escalation triggers (rule)",
            "Business description vs sector alignment (hybrid)",
            "Transaction profile vs business scale (hybrid)",
            "Recommendation narrative (hybrid)",
            "Revenue model plausibility (ai)", "Business model plausibility (ai)",
            "Compliance memo drafting (ai)",
        ],
    },
    6: {
        "description": (
            "Rule-based review preparation with hybrid priority scoring. Scans document expiry, "
            "ownership changes, screening staleness, outstanding alerts; assembles review package "
            "with priority score. Degraded mode when no prior review history exists."
        ),
        "checks": [
            "Review schedule compliance check (rule)",
            "Risk level change detection (rule)",
            "Document expiry scan (rule)",
            "Ownership structure change detection (rule)",
            "Screening data staleness check (rule)",
            "Activity volume comparison (rule)",
            "Outstanding alert aggregation (rule)",
            "Regulatory requirement completeness (rule)",
            "Review priority scoring (hybrid)",
            "Review package assembly (hybrid)",
        ],
    },
    7: {
        "description": (
            "Monitoring interpreter with AI narrative. Retrieves new media/PEP/sanctions signals, "
            "deduplicates, scores severity, resolves entities; AI generates narrative summary and "
            "disposition. Degraded mode when no screening baseline exists."
        ),
        "checks": [
            "New adverse media retrieval (rule)",
            "PEP status change detection (rule)",
            "Sanctions list update check (rule)",
            "Media source credibility scoring (rule)",
            "Alert deduplication (rule)",
            "Historical media comparison (rule)",
            "Media severity assessment (hybrid)",
            "PEP proximity scoring (hybrid)",
            "Entity resolution for media hits (hybrid)",
            "Combined risk signal aggregation (hybrid)",
            "Media narrative summarisation (ai)",
            "Monitoring alert disposition (ai)",
        ],
    },
    8: {
        "description": (
            "Rule-based drift detection with hybrid scoring. Compares transaction volume, geographic "
            "activity, counterparty concentration, product usage against onboarding baseline; scores "
            "velocity anomalies and peer deviation. Degraded mode when no transaction data available."
        ),
        "checks": [
            "Transaction volume baseline comparison (rule)",
            "Geographic activity deviation (rule)",
            "Counterparty concentration check (rule)",
            "Product usage deviation (rule)",
            "Dormancy/reactivation detection (rule)",
            "Threshold breach detection (rule)",
            "Velocity anomaly scoring (hybrid)",
            "Peer group deviation analysis (hybrid)",
            "Temporal pattern drift detection (hybrid)",
            "Multi-dimensional risk drift scoring (hybrid)",
            "Drift narrative and recommendation (hybrid)",
        ],
    },
    9: {
        "description": (
            "Detects when regulatory changes affect existing clients, "
            "tracks jurisdiction-specific regulations, and alerts on compliance requirement updates."
        ),
        "checks": [
            "Regulatory change monitoring", "Impact assessment on client portfolio",
            "Jurisdiction-specific regulation tracking", "Compliance requirement updates",
            "Client-specific regulatory alerts",
        ],
    },
    10: {
        "description": (
            "Consolidation agent with AI narrative. Verifies document currency, screening recency, "
            "policy applicability, condition compliance, filing deadlines; consolidates inter-agent "
            "findings; AI generates compliance narrative and escalation/closure recommendation. "
            "Degraded mode when upstream agents have not run."
        ),
        "checks": [
            "Document currency verification (rule)",
            "Screening recency check (rule)",
            "Policy change applicability check (rule)",
            "Condition compliance tracking (rule)",
            "Filing deadline monitoring (rule)",
            "Inter-agent finding consolidation (rule)",
            "Remediation tracker status (rule)",
            "Compliance risk re-scoring (hybrid)",
            "Review frequency recommendation (hybrid)",
            "Compliance narrative generation (ai)",
            "Escalation/closure recommendation (ai)",
        ],
    },
}


def _migrate_agent_definitions(db: DBConnection):
    """Upsert agent definitions to match Wave 1-4 implementations.

    Uses UPDATE for existing rows; if a row is missing (e.g. demo DB was
    cleared), falls back to INSERT so the agent is recreated.
    """
    for agent_num, defn in _AGENT_DEFINITIONS_V2.items():
        db.execute(
            "UPDATE ai_agents SET description=?, checks=? WHERE agent_number=?",
            (defn["description"], json.dumps(defn["checks"]), agent_num)
        )
        # If UPDATE matched nothing, the row is missing — insert it
        # db.execute() returns self (DBConnection), cursor is internal
        rows_affected = getattr(db._cursor, "rowcount", -1) if db._cursor else -1
        if rows_affected == 0:
            try:
                meta = _AGENT_METADATA.get(agent_num, (f"Agent {agent_num}", "🤖", "Onboarding", None, []))
                db.execute(
                    "INSERT INTO ai_agents (agent_number, name, icon, stage, description, enabled, checks, supervisor_agent_type, risk_dimensions) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (agent_num, meta[0], meta[1], meta[2],
                     defn["description"], True, json.dumps(defn["checks"]),
                     meta[3], json.dumps(meta[4]))
                )
                logger.info(f"Inserted missing agent {agent_num} via migration")
            except Exception as e:
                logger.warning(f"Could not insert agent {agent_num}: {e}")
    db.commit()
    logger.info("Migrated agent definitions to Wave 1-4 versions")


def _seed_monitoring_demo_data(db: DBConnection):
    """Seed monitoring, periodic review, and EDD demo data (idempotent — checks for empty tables)."""
    _is_demo = _CFG_IS_DEMO
    try:
        from environment import is_demo as _env_is_demo
        _is_demo = _is_demo or _env_is_demo()
    except ImportError:
        pass
    if not _is_demo:
        return

    now = datetime.now()

    # --- H-1: Deduplicate monitoring agents (cleanup from prior double-seed) ---
    try:
        if USE_POSTGRESQL:
            db.execute("""
                DELETE FROM monitoring_agent_status
                WHERE id NOT IN (
                    SELECT MIN(id) FROM monitoring_agent_status GROUP BY agent_name
                )
            """)
        else:
            db.execute("""
                DELETE FROM monitoring_agent_status
                WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM monitoring_agent_status GROUP BY agent_name
                )
            """)
        db.commit()
        logger.info("H-1: Agent dedup cleanup completed")
    except Exception as e:
        logger.warning(f"Agent dedup cleanup skipped: {e}")

    # --- H-2: Ensure demo application stubs exist (monitoring/EDD data references these) ---
    try:
        demo_app_stubs = [
            ("demo-scenario-01", "ARF-2026-DEMO01", "Meridian Software Ltd"),
            ("demo-scenario-02", "ARF-2026-DEMO02", "Coral Bay Holdings Ltd"),
            ("demo-scenario-03", "ARF-2026-DEMO03", "Atlas Digital Assets DMCC"),
            ("demo-scenario-04", "ARF-2026-DEMO04", "Sunshine Trading Co"),
            ("demo-scenario-05", "ARF-2026-DEMO05", "Levant Global Enterprises S.A.L."),
        ]
        for app_id, ref, company in demo_app_stubs:
            db.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name, status) VALUES (?, ?, ?, 'submitted')",
                (app_id, ref, company)
            )
        db.commit()
        logger.info("H-2: Demo application stubs ensured")
    except Exception as e:
        logger.warning(f"Demo application stub insertion skipped: {e}")

    # Only seed each table if it's empty — prevents duplicates on restart
    alerts_count = db.execute("SELECT COUNT(*) as c FROM monitoring_alerts").fetchone()["c"]
    reviews_count = db.execute("SELECT COUNT(*) as c FROM periodic_reviews").fetchone()["c"]
    agents_count = db.execute("SELECT COUNT(*) as c FROM monitoring_agent_status").fetchone()["c"]
    try:
        edd_count = db.execute("SELECT COUNT(*) as c FROM edd_cases").fetchone()["c"]
    except Exception:
        edd_count = 0  # table may not exist yet on older schemas

    if agents_count == 0:
        logger.info("Demo mode: seeding sample monitoring agent status")
        now_iso = now.isoformat()
        next_day = (now + timedelta(days=1)).isoformat()
        next_week = (now + timedelta(days=7)).isoformat()
        next_month = (now + timedelta(days=30)).isoformat()
        agents_status = [
            ("Sanctions/PEP Agent", "sanctions_pep", now_iso, next_day, "Daily", 45, 2, "active"),
            ("Adverse Media Agent", "adverse_media", now_iso, (now + timedelta(hours=6)).isoformat(), "Every 6 hours", 45, 1, "active"),
            ("Registry Monitoring Agent", "registry", (now - timedelta(days=7)).isoformat(), next_week, "Weekly", 45, 0, "active"),
            ("Risk Drift Agent", "risk_drift", (now - timedelta(days=30)).isoformat(), next_month, "Monthly", 45, 3, "active"),
            ("Regulatory Impact Agent", "regulatory", (now - timedelta(days=14)).isoformat(), next_month, "On circular publication", 45, 1, "active"),
        ]
        for agent_data in agents_status:
            db.execute(
                "INSERT INTO monitoring_agent_status (agent_name, agent_type, last_run, next_run, run_frequency, clients_monitored, alerts_generated, status) VALUES (?,?,?,?,?,?,?,?)",
                agent_data
            )

    if alerts_count == 0:
        logger.info("Demo mode: seeding sample monitoring alerts")
        demo_alerts = [
            ("demo-scenario-03", "Atlas Digital Assets DMCC", "Sanctions Match", "Critical",
             "Sanctions/PEP Agent", "Potential sanctions match detected for director Hassan Osman — name appears on updated OFAC SDN list entry (similarity: 92%). Requires immediate review.",
             "OFAC SDN List Update 2026-03-15", "Immediately escalate to MLRO. Suspend onboarding pending verification. Consider SAR filing if match confirmed.", "open"),
            ("demo-scenario-03", "Atlas Digital Assets DMCC", "PEP Status Change", "High",
             "Sanctions/PEP Agent", "PEP status change detected: Hassan Osman — new media reports indicate appointment as economic adviser to Nigerian federal government, effective March 2026.",
             "Dow Jones PEP Database", "Review updated PEP declaration. Assess whether new role increases corruption/bribery risk. Update risk profile.", "open"),
            ("demo-scenario-02", "Coral Bay Holdings Ltd", "Adverse Media", "High",
             "Adverse Media Agent", "Adverse media detected: Pierre Leclerc named in French financial press regarding offshore tax avoidance investigation (Le Monde, 2026-03-20).",
             "Adverse Media Scan — Le Monde", "Obtain details of investigation. Assess relevance to client relationship. Consider enhanced monitoring.", "open"),
            ("demo-scenario-05", "Levant Global Enterprises S.A.L.", "Registry Change", "Medium",
             "Registry Monitoring Agent", "Company registry update: Levant Global registered new branch office in Beirut, Lebanon. Expanded geographic footprint in high-risk jurisdictions.",
             "Lebanon Commercial Registry", "Review expanded operations scope. Assess whether new branch triggers additional regulatory obligations.", "escalated"),
            ("demo-scenario-01", "Meridian Software Ltd", "Risk Drift", "Low",
             "Risk Drift Agent", "Minor risk drift detected: Meridian Software added new operating country (Germany). No material risk impact — EU jurisdiction, low incremental risk.",
             "UK Companies House Filing", "No immediate action required. Update country list at next periodic review.", "dismissed"),
            ("demo-scenario-04", "Sunshine Trading Co", "Regulatory Impact", "Medium",
             "Regulatory Impact Agent", "New AML/CFT guideline from Bank of Mauritius (BOM Circular 2026/03) may affect Import/Export sector compliance requirements. Review applicability.",
             "BOM Circular 2026/03", "Review circular for applicability. Assess whether current CDD measures are sufficient under new guidelines.", "open"),
        ]
        for alert_data in demo_alerts:
            offset_days = demo_alerts.index(alert_data) * 3 + 1
            created = (now - timedelta(days=offset_days)).isoformat()
            db.execute("""
                INSERT INTO monitoring_alerts
                    (application_id, client_name, alert_type, severity, detected_by, summary, source_reference, ai_recommendation, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (*alert_data, created))

    if reviews_count == 0:
        logger.info("Demo mode: seeding sample periodic reviews")
        demo_reviews = [
            ("demo-scenario-03", "Atlas Digital Assets DMCC", "HIGH", "time_based",
             "Quarterly review — HIGH risk client with PEP exposure", "pending",
             (now - timedelta(days=5)).strftime("%Y-%m-%d")),
            ("demo-scenario-02", "Coral Bay Holdings Ltd", "MEDIUM", "time_based",
             "Semi-annual review — MEDIUM risk offshore holding", "pending",
             (now + timedelta(days=10)).strftime("%Y-%m-%d")),
            ("demo-scenario-05", "Levant Global Enterprises S.A.L.", "VERY_HIGH", "alert_triggered",
             "Review triggered by sanctions screening alert", "pending",
             (now - timedelta(days=12)).strftime("%Y-%m-%d")),
            ("demo-scenario-01", "Meridian Software Ltd", "LOW", "time_based",
             "Annual review — LOW risk technology company", "pending",
             (now + timedelta(days=180)).strftime("%Y-%m-%d")),
            ("demo-scenario-04", "Sunshine Trading Co", "MEDIUM", "time_based",
             "Semi-annual review — incomplete documentation history", "completed",
             (now - timedelta(days=30)).strftime("%Y-%m-%d")),
        ]
        for rev_data in demo_reviews:
            completed_at = now.isoformat() if rev_data[5] == "completed" else None
            decision = "continue" if rev_data[5] == "completed" else None
            db.execute("""
                INSERT INTO periodic_reviews
                    (application_id, client_name, risk_level, trigger_type, trigger_reason, status, due_date, completed_at, decision)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (rev_data[0], rev_data[1], rev_data[2], rev_data[3], rev_data[4], rev_data[5], rev_data[6], completed_at, decision))

    if edd_count == 0:
        logger.info("Demo mode: seeding sample EDD cases")
        demo_edd = [
            ("demo-scenario-03", "Atlas Digital Assets DMCC", "HIGH", 72.5, "analysis",
             "officer_decision", "PEP exposure + crypto sector. Escalated from compliance review.",
             json.dumps([
                 {"ts": (now - timedelta(days=8)).isoformat(), "author": "System", "note": "EDD triggered: risk score 72.5 exceeds threshold. PEP director detected."},
                 {"ts": (now - timedelta(days=6)).isoformat(), "author": "Marie Dubois", "note": "Source of funds documentation requested from applicant."},
                 {"ts": (now - timedelta(days=2)).isoformat(), "author": "Marie Dubois", "note": "SOF documentation received. Analysing trading revenue claims against bank statements."},
             ]),
             (now - timedelta(days=8)).isoformat()),
            ("demo-scenario-05", "Levant Global Enterprises S.A.L.", "VERY_HIGH", 91.0, "pending_senior_review",
             "officer_decision", "Sanctioned jurisdiction + shell structure. Immediate EDD required.",
             json.dumps([
                 {"ts": (now - timedelta(days=15)).isoformat(), "author": "System", "note": "EDD triggered: VERY_HIGH risk — Syria jurisdiction, shell entity, opaque ownership."},
                 {"ts": (now - timedelta(days=12)).isoformat(), "author": "Aisha Sudally", "note": "Full enhanced screening completed. Multiple red flags confirmed."},
                 {"ts": (now - timedelta(days=10)).isoformat(), "author": "Aisha Sudally", "note": "Analysis complete. Recommending REJECT. Submitted for senior review."},
             ]),
             (now - timedelta(days=15)).isoformat()),
        ]
        for edd_data in demo_edd:
            db.execute("""
                INSERT INTO edd_cases
                    (application_id, client_name, risk_level, risk_score, stage, trigger_source, trigger_notes, edd_notes, triggered_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, edd_data)

    db.commit()
    logger.info("Demo mode: monitoring demo data seeding complete")


def _ensure_document_health_monitor_agent(db: DBConnection):
    """Ensure the operational document-health monitor is registered.

    Unlike demo monitoring rows, this agent backs a real scanner used by
    staging/production workflows and must exist even when demo seeding is off.
    """
    existing = db.execute(
        """
        SELECT id FROM monitoring_agent_status
         WHERE agent_type = ? OR agent_name = ?
         ORDER BY id ASC LIMIT 1
        """,
        ("document_health", "Document Health Monitor"),
    ).fetchone()
    if existing:
        db.execute(
            """
            UPDATE monitoring_agent_status
               SET agent_name = ?,
                   agent_type = ?,
                   run_frequency = COALESCE(run_frequency, ?),
                   clients_monitored = COALESCE(clients_monitored, 0),
                   alerts_generated = COALESCE(alerts_generated, 0),
                   status = CASE
                       WHEN status IS NULL OR status = '' OR status IN ('inactive','disabled')
                       THEN 'enabled'
                       ELSE status
                   END
             WHERE id = ?
            """,
            (
                "Document Health Monitor",
                "document_health",
                "Manual / daily",
                existing["id"],
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO monitoring_agent_status
                (agent_name, agent_type, last_run, next_run, run_frequency,
                 clients_monitored, alerts_generated, status)
            VALUES (?, ?, NULL, NULL, ?, 0, 0, ?)
            """,
            (
                "Document Health Monitor",
                "document_health",
                "Manual / daily",
                "enabled",
            ),
        )
    db.commit()


QA_SMOKE_USER_ID = "github-actions:day6-staging-smoke"
QA_SMOKE_USER_EMAIL = "github-actions-day6-staging-smoke@onboarda.internal"
QA_SMOKE_USER_NAME = "GitHub Day 6 Staging Smoke"
QA_SMOKE_USER_ROLE = "sco"


def _should_seed_qa_smoke_user() -> bool:
    environment = (_CFG_ENVIRONMENT or "").strip().lower()
    return environment in {"staging", "testing", "test"}


def ensure_qa_smoke_user(db: DBConnection) -> bool:
    """Ensure the deterministic staging smoke token subject maps to an active user."""
    if not _should_seed_qa_smoke_user():
        return False

    import bcrypt

    no_login_password = secrets.token_urlsafe(48)
    pw_hash = bcrypt.hashpw(no_login_password.encode(), bcrypt.gensalt()).decode()
    db.execute(
        """
        INSERT OR IGNORE INTO users
            (id, email, password_hash, full_name, role, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            QA_SMOKE_USER_ID,
            QA_SMOKE_USER_EMAIL,
            pw_hash,
            QA_SMOKE_USER_NAME,
            QA_SMOKE_USER_ROLE,
        ),
    )
    db.execute(
        """
        UPDATE users
           SET full_name = ?,
               role = ?,
               status = 'active',
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (QA_SMOKE_USER_NAME, QA_SMOKE_USER_ROLE, QA_SMOKE_USER_ID),
    )
    db.commit()
    logger.info(
        "QA smoke user ensured for environment=%s id=%s role=%s",
        _CFG_ENVIRONMENT,
        QA_SMOKE_USER_ID,
        QA_SMOKE_USER_ROLE,
    )
    return True


# Default GDPR data-retention policies (Sprint 3; Mauritius Data Protection
# Act 2017 + GDPR Article 5(1)(e)). Seeded by _ensure_retention_policies().
# The auto_purge / requires_review values are Python booleans, NOT 0/1
# integers: PostgreSQL rejects integer literals bound to BOOLEAN columns,
# and this seed had never actually executed against PostgreSQL before PR-31
# (see _ensure_retention_policies docstring).
_DEFAULT_RETENTION_POLICIES = [
    ("client_pii", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "Client personal data: names, addresses, DOB, nationality. 7 years post-relationship.", False, True),
    ("kyc_documents", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "KYC/CDD documents: passports, proof of address, corporate registry. 7 years post-relationship.", False, True),
    ("screening_results", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "Sanctions, PEP, adverse media screening results. 7 years retention.", False, True),
    ("compliance_memos", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "Compliance memos and risk assessments. 7 years retention.", False, True),
    ("audit_logs", 3650, "Legitimate interest + regulatory", "Audit trail records. 10 years retention for full accountability.", False, False),
    ("application_data", 2555, "Regulatory obligation", "Onboarding application forms and submitted data. 7 years post-decision.", False, True),
    ("sar_reports", 3650, "Regulatory obligation (FIU reporting)", "Suspicious Activity Reports. 10 years — never auto-purge.", False, False),
    # auto_purge is False (audit finding B1): this policy previously carried
    # auto_purge=1 while resolving to the audit_log table, so the daily
    # scheduler was destroying the audit trail. Token cleanup is not wired to
    # a real table here; the policy is retained for documentation only and
    # must never auto-purge.
    ("session_tokens", 1, "Legitimate interest", "Expired authentication tokens and session data. 24-hour retention (documentation only; not auto-purged).", False, False),
    ("monitoring_alerts", 2555, "Regulatory obligation", "Ongoing monitoring alerts and risk drift records. 7 years.", False, True),
]


def _ensure_retention_policies(db: DBConnection) -> int:
    """Insert any missing default data-retention policies (idempotent).

    Audit follow-up PR-31: this seed used to live at the BOTTOM of
    seed_initial_data(), after the "Database already seeded" early return —
    so any database whose core tables were populated before the block was
    added (staging) exited early on every boot and the
    data_retention_policies table stayed empty. It now runs on BOTH seed
    paths, like the other post-initial-seed ensures.

    Inserts missing categories only (INSERT OR IGNORE against the UNIQUE
    data_category); existing rows are never updated, so operator-modified
    policies — and the session_tokens auto_purge=FALSE invariant from audit
    finding B1 / migration 039 — survive re-seeding.

    Returns the number of rows inserted.
    """
    def _count():
        row = db.execute("SELECT COUNT(*) AS c FROM data_retention_policies").fetchone()
        return int(dict(row).get("c") or 0) if row else 0

    before = _count()
    failures = 0
    for policy in _DEFAULT_RETENTION_POLICIES:
        try:
            db.execute(
                "INSERT OR IGNORE INTO data_retention_policies (data_category, retention_days, legal_basis, description, auto_purge, requires_review) VALUES (?,?,?,?,?,?)",
                policy
            )
        except Exception as e:
            failures += 1
            logger.error(f"Data retention policy '{policy[0]}' ensure failed: {e}")
    if failures == len(_DEFAULT_RETENTION_POLICIES):
        # Every insert failed — the table is likely missing or misconfigured.
        # Loud by design: a silently-empty policy table is the exact failure
        # mode this function exists to prevent.
        logger.error(
            "All %d retention-policy inserts failed — data_retention_policies "
            "may be missing or misconfigured; retention enforcement has no "
            "policies to act on",
            failures,
        )
    inserted = _count() - before
    if inserted > 0:
        db.commit()
        logger.info(f"Retention policies ensured: {inserted} missing default(s) inserted")
    return inserted


def _seed_account_secret(role: str, admin_password):
    """Pick the seed secret for one account (PR-25).

    The admin account honours an operator-provided ADMIN_INITIAL_PASSWORD; every
    other account, and the admin when that value is empty, gets a DISTINCT random
    secret. Returns (secret, is_generated) — is_generated is True only for the
    random ones, which must be delivered out-of-band (never logged)."""
    if role == "admin" and admin_password:
        return admin_password, False
    return secrets.token_urlsafe(16), True


def _seeded_credentials_target(generated: dict, env, is_demo: bool):
    """Pure delivery decision (no side effects, unit-testable): returns
    ('file', path) for dev/demo, ('warn', None) for staging/production, or
    ('skip', None) when there is nothing to deliver. The pytest/test no-op guard
    is applied by the caller, NOT here, so this branch logic stays covered."""
    if not generated:
        return ("skip", None)
    env = (env or "").strip().lower()
    if is_demo or env in ("development", "demo"):
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
        return ("file", os.path.join(base, ".seeded_credentials"))
    return ("warn", None)


def _write_seeded_credentials_file(generated: dict, path: str) -> None:
    """Write the seed credentials to ``path`` (0600) — unit-testable side effect."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for email, secret in generated.items():
            fh.write(f"{email}\t{secret}\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _deliver_seeded_credentials(generated: dict) -> None:
    """Deliver randomly-generated seed credentials WITHOUT logging the secrets (PR-25).

    - Under pytest (or ENVIRONMENT=test/testing): no side effects at all — the
      ~40 tests that call seed_initial_data must not write files or emit noise.
    - dev/demo: write ``uploads/.seeded_credentials`` (that directory is
      gitignored) with 0600 perms so a local operator can log in, and log a
      pointer to the file — never the secrets themselves.
    - staging/production: log only a warning that accounts have distinct random
      secrets needing a reset; never write plaintext, never log the secrets.

    Best-effort: a delivery failure must never break seeding.
    """
    if not generated:
        return
    try:
        import sys
        env = (_CFG_ENVIRONMENT or "").strip().lower()
        if "pytest" in sys.modules or env in ("test", "testing"):
            return
        mode, path = _seeded_credentials_target(generated, _CFG_ENVIRONMENT, _CFG_IS_DEMO)
        if mode == "file":
            _write_seeded_credentials_file(generated, path)
            logger.warning(
                "PR-25: %d seeded account(s) received distinct random secrets; written to "
                "uploads/.seeded_credentials (gitignored, 0600) — rotate after first login.",
                len(generated),
            )
        elif mode == "warn":
            logger.warning(
                "PR-25: %d seeded account(s) were created with distinct random secrets "
                "(no operator-provided password); reset them via the password-reset flow. "
                "Secrets are NOT logged.",
                len(generated),
            )
    except Exception as exc:
        logger.warning("PR-25: could not deliver seeded credentials: %s", exc)


def seed_initial_data(db: DBConnection):
    """Seed database with initial admin users, risk config, and AI agents."""
    import bcrypt

    # Check each table independently — allows partial re-seeding if some tables failed
    users_count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    agents_count = db.execute("SELECT COUNT(*) as c FROM ai_agents").fetchone()["c"]
    checks_count = db.execute("SELECT COUNT(*) as c FROM ai_checks").fetchone()["c"]
    risk_count = db.execute("SELECT COUNT(*) as c FROM risk_config").fetchone()["c"]

    # --- Migration: upsert agent definitions (Wave 1-4 alignment) ---
    # Always run: inserts missing agents AND updates existing ones
    _migrate_agent_definitions(db)

    if users_count > 0 and agents_count > 0 and checks_count > 0 and risk_count > 0:
        logger.info("Database already seeded, skipping core initialization")
        ensure_qa_smoke_user(db)
        # Still check if monitoring demo data needs seeding (added post-initial-seed)
        _seed_monitoring_demo_data(db)
        _ensure_document_health_monitor_agent(db)
        # PR-31: retention policies were previously seeded only below the early
        # return, so already-seeded databases (staging) never received them.
        _ensure_retention_policies(db)
        return

    logger.info(f"Seed status: users={users_count}, agents={agents_count}, checks={checks_count}, risk={risk_count}")

    # === USERS ===
    if users_count == 0:
        # PR-25 (M14): each seeded account gets its OWN secret and bcrypt hash —
        # never a single shared privileged bootstrap credential — and generated
        # secrets are NEVER printed to stdout/logs (CloudWatch would retain
        # them). The admin account honours ADMIN_INITIAL_PASSWORD when set; every
        # other account (and the admin when that env var is unset) gets a
        # distinct random secret, delivered out-of-band by
        # _deliver_seeded_credentials.
        _seeded_accounts = [
            ("admin001", "asudally@onboarda.com", "Aisha Sudally", "admin"),
            ("sco001", "raj.patel@onboarda.com", "Raj Patel", "sco"),
            ("co001", "m.dubois@onboarda.com", "Marie Dubois", "co"),
            ("analyst001", "l.wei@onboarda.com", "Li Wei", "analyst"),
        ]
        _generated_credentials = {}  # email -> secret, only for randomly generated ones
        for _uid, _email, _name, _role in _seeded_accounts:
            _secret, _is_generated = _seed_account_secret(_role, _CFG_ADMIN_INITIAL_PASSWORD)
            if _is_generated:
                _generated_credentials[_email] = _secret
            _acct_hash = bcrypt.hashpw(_secret.encode(), bcrypt.gensalt()).decode()
            db.execute(
                "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
                (_uid, _email, _acct_hash, _name, _role, "active"),
            )
        db.commit()
        _deliver_seeded_credentials(_generated_credentials)
        logger.info("Users seeded (%d accounts, distinct per-account secrets)", len(_seeded_accounts))
    ensure_qa_smoke_user(db)

    # === RISK CONFIG ===
    if risk_count == 0:
        logger.info("Seeding risk config...")
        default_dims = json.dumps([
        {
            "id": "D1", "name": "Customer / Entity Risk", "weight": 30,
            "subcriteria": [
                {"name": "Entity Type", "weight": 20},
                {"name": "Ownership Structure", "weight": 20},
                {"name": "PEP Status", "weight": 25},
                {"name": "Adverse Media", "weight": 15},
                {"name": "Source of Wealth", "weight": 10},
                {"name": "Source of Funds", "weight": 10}
            ]
        },
        {
            "id": "D2", "name": "Geographic Risk", "weight": 25,
            "subcriteria": [
                {"name": "Country of Incorporation", "weight": 25},
                {"name": "UBO Nationalities", "weight": 20},
                {"name": "Intermediary Shareholder Jurisdictions", "weight": 20},
                {"name": "Countries of Operation", "weight": 20},
                {"name": "Target Markets", "weight": 15}
            ]
        },
        {
            "id": "D3", "name": "Product / Service Risk", "weight": 20,
            "subcriteria": [
                {"name": "Service Type", "weight": 40},
                {"name": "Monthly Volume", "weight": 35},
                {"name": "Transaction Complexity", "weight": 25}
            ]
        },
        {
            "id": "D4", "name": "Industry / Sector Risk", "weight": 15,
            "subcriteria": [
                {"name": "Industry Sector", "weight": 100}
            ]
        },
        {
            "id": "D5", "name": "Delivery Channel Risk", "weight": 10,
            "subcriteria": [
                {"name": "Introduction Method", "weight": 50},
                {"name": "Delivery Channel", "weight": 50}
            ]
        }
        ])
        default_thresholds = json.dumps([
        {"level": "LOW", "min": 0, "max": 39.9},
        {"level": "MEDIUM", "min": 40, "max": 54.9},
        {"level": "HIGH", "min": 55, "max": 69.9},
        {"level": "VERY_HIGH", "min": 70, "max": 100}
        ])
        default_country_scores = json.dumps({
        # v1.6: Aligned with ARIE_Risk_Score_Sheet v1.6 (80+ countries)
        # Score 1 — Low Risk (FATF members, strong AML)
        "australia": 1, "canada": 1, "france": 1, "germany": 1, "hong kong": 1,
        "ireland": 1, "japan": 1, "luxembourg": 1, "netherlands": 1, "new zealand": 1,
        "singapore": 1, "switzerland": 1, "united kingdom": 1, "united states": 1,
        "austria": 1, "belgium": 1, "denmark": 1, "finland": 1, "norway": 1,
        "sweden": 1, "south korea": 1, "israel": 1, "iceland": 1, "italy": 1,
        "portugal": 1, "spain": 1, "taiwan": 1, "uk": 1, "usa": 1,
        # Score 2 — Medium Risk (FATF members, emerging/standard)
        "bahrain": 2, "botswana": 2, "brazil": 2, "chile": 2, "china": 2,
        "india": 2, "indonesia": 2, "kuwait": 2, "malaysia": 2, "mauritius": 2,
        "mexico": 2, "morocco": 2, "oman": 2, "qatar": 2, "rwanda": 2,
        "saudi arabia": 2, "turkey": 2, "uae": 2,
        "uganda": 2, "ghana": 2, "ivory coast": 2, "jordan": 2, "sri lanka": 2, "tunisia": 2,
        "jersey": 2, "guernsey": 2, "isle of man": 2, "liechtenstein": 2,
        "estonia": 2, "pakistan": 2, "seychelles": 2,
        # Score 3 — High Risk (FATF grey list, offshore/secrecy)
        "algeria": 3, "burkina faso": 3, "cameroon": 3, "democratic republic of congo": 3,
        "haiti": 3, "kenya": 3, "laos": 3, "lebanon": 3, "mali": 3, "monaco": 3,
        "mozambique": 3, "nigeria": 3, "philippines": 3, "senegal": 3, "south africa": 3,
        "south sudan": 3, "tanzania": 3, "venezuela": 3, "vietnam": 3, "yemen": 3,
        "bermuda": 3, "vanuatu": 3, "samoa": 3, "marshall islands": 3, "iraq": 3,
        # Score 4 — Very High Risk (FATF black list, sanctioned, secrecy jurisdictions)
        "iran": 4, "north korea": 4, "myanmar": 4, "russia": 4, "syria": 4, "belarus": 4,
        "cuba": 4, "crimea": 4, "afghanistan": 4, "somalia": 4, "libya": 4, "eritrea": 4, "sudan": 4,
        "bvi": 4, "british virgin islands": 4, "cayman islands": 4, "panama": 4
        })
        default_sector_scores = json.dumps({
        "regulated financial": 1, "government": 1, "bank": 1, "listed company": 1,
        "agriculture": 1, "education": 1,
        "healthcare": 2, "technology": 2, "software": 2, "saas": 2, "manufacturing": 2,
        "retail": 2, "e-commerce": 2, "media": 2, "logistics": 2, "insurance": 2,
        "telecommunications": 2, "banking": 2,
        "construction": 3, "import": 3, "export": 3, "real estate": 3, "mining": 3,
        "oil": 3, "gas": 3, "energy": 3, "money services": 3, "forex": 3, "precious": 3,
        "non-profit": 3, "ngo": 3, "charity": 3, "advisory": 3,
        "management consulting": 3, "consulting": 3, "financial / tax advisory": 3,
        "fintech": 3, "e-money": 3, "legal": 3, "accounting": 3, "shipping": 3, "maritime": 3,
        "crypto": 4, "virtual asset": 4, "gambling": 4, "gaming": 4, "betting": 4,
        "arms": 4, "defence": 4, "military": 4, "shell company": 4, "nominee": 4,
        "precious metals": 4
        })
        default_entity_scores = json.dumps({
        "listed company": 1, "regulated financial institution": 1, "regulated fi": 1,
        "regulated entity": 1, "government": 1, "government body": 1, "public sector": 1,
        "listed": 1, "regulated": 1,
        "large private company": 2, "large private": 2, "sme": 2, "private company": 2,
        "regulated fund": 2,
        "newly incorporated": 3, "trust": 3, "foundation": 3, "ngo": 3, "non-profit": 3,
        "unregulated fund": 4, "spv": 4, "shell company": 4, "shell": 4
        })
        db.execute(
            "INSERT INTO risk_config (id, dimensions, thresholds, country_risk_scores, sector_risk_scores, entity_type_scores) VALUES (?, ?, ?, ?, ?, ?)",
            (1, default_dims, default_thresholds, default_country_scores, default_sector_scores, default_entity_scores)
        )
        db.commit()
        logger.info("Risk config seeded")

    # === AI AGENTS ===
    if agents_count == 0:
        agent1_checks = json.dumps([
        "Configured document verification checks",
        "Upload gate checks",
        "Material issue detection",
        "Requested EDD evidence routing",
        "Manual-review-only evidence routing",
        "Workflow blocker mapping",
        "Follow-up requirement markers",
        "Unknown document review routing",
        "Audit/export detail preservation",
    ])
        agent2_checks = json.dumps([
            "Registry source selection (rule)",
            "Company registration number lookup (rule)",
            "Entity name match to registry (rule)",
            "Incorporation date match (rule)",
            "Company status check (rule)",
            "Jurisdiction match (rule)",
            "Company type / legal form (rule)",
            "Registered address match (hybrid)",
            "Director names cross-check (hybrid)",
            "Shareholder names cross-check (hybrid)",
            "UBO declarations vs registry shareholders (hybrid)",
            "Registry filing recency / availability (rule)",
            "Interpretation of unusual registry output (hybrid)"
        ])
        agent3_checks = json.dumps([
            "Sanctions hit retrieval (rule)",
            "PEP hit retrieval (rule)",
            "Adverse media hit retrieval (rule)",
            "Exact identity disambiguation (rule)",
            "Near-match identity disambiguation (hybrid)",
            "False-positive reduction (hybrid)",
            "Severity ranking of confirmed hits (hybrid)",
            "Adverse media relevance assessment (ai)",
            "Adverse media materiality / seriousness (ai)",
            "Consolidated screening narrative (ai)",
            "Recommended screening disposition (hybrid)"
        ])
        agent4_checks = json.dumps([
            "Direct ownership calculation (rule)", "Indirect ownership via intermediaries (rule)",
            "UBO threshold qualification ≥25% (rule)", "Total ownership completeness (rule)",
            "Circular ownership detection (rule)", "Nominee arrangement detection (rule)",
            "Trust/foundation structure detection (rule)", "Holding company/SPV detection (rule)",
            "Opaque jurisdiction flagging (rule)", "Shell company indicator aggregation (rule)",
            "Complexity scoring (rule)", "Ownership arithmetic validation (rule)",
            "Escalation logic (rule)"
        ])
        agent5_checks = json.dumps([
            "Document completeness score (rule)", "Jurisdiction risk score (rule)",
            "Industry/sector risk score (rule)", "Product/service risk score (rule)",
            "Channel/delivery risk score (rule)", "Ownership complexity ingestion (rule)",
            "Screening severity ingestion (rule)", "Weighted total risk score (rule)",
            "Risk tier bucket (rule)", "Mandatory escalation triggers (rule)",
            "Business description vs sector alignment (hybrid)",
            "Transaction profile vs business scale (hybrid)",
            "Recommendation narrative (hybrid)",
            "Revenue model plausibility (ai)", "Business model plausibility (ai)",
            "Compliance memo drafting (ai)"
        ])
    
        agents_seed = [
            (
                1, "Identity & Document Integrity Agent", "🔍", "Onboarding",
                "Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured in Document Verification Policies. "
                "It can verify, flag, block reliance, recommend officer action, and trigger required follow-up. "
                "It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening.",
                1, agent1_checks
            ),
            (
                2, "External Database Cross-Verification Agent", "🔎", "Onboarding",
                "Rule-based registry verification with provider abstraction. Checks company identity data against external registries "
                "(OpenCorporates, Companies House, CBRD, ADGM, DIFC). Runs in degraded mode when no external API credentials are configured.",
                1, agent2_checks
            ),
            (
                3, "FinCrime Screening Interpretation Agent", "💼", "Onboarding",
                "Policy-bounded screening interpreter. Reads stored screening results from prescreening_data. "
                "4 rule-based checks (retrieval, disambiguation), 4 hybrid (FP reduction, severity ranking, disposition), "
                "3 AI (adverse media assessment, narrative). Degraded mode when no screening report available.",
                1, agent3_checks
            ),
            (
                4, "Corporate Structure & UBO Mapping Agent", "🏗️", "Onboarding",
                "Rule-based ownership mapping with indirect path tracking, circular ownership detection, "
                "nominee/trust/holding detection, and complexity scoring. All checks are deterministic — no AI calls.",
                1, agent4_checks
            ),
            (
                5, "Compliance Memo & Risk Recommendation Agent", "📝", "Onboarding",
                "Unified compliance memo agent. Bridges to authoritative memo path enforcing Rules 4A-4E, "
                "computing 7 risk dimensions, and generating an 11-section memo. Classification-tagged output "
                "(rule/hybrid/ai). Includes risk-model divergence cross-check.",
                1, agent5_checks
            ),
            (
                6, "Periodic Review Preparation Agent", "📅", "Monitoring",
                "Rule-based review preparation with hybrid priority scoring. Scans document expiry, "
                "ownership changes, screening staleness, outstanding alerts; assembles review package "
                "with priority score. Degraded mode when no prior review history exists.",
                1, json.dumps([
                    "Review schedule compliance check (rule)",
                    "Risk level change detection (rule)",
                    "Document expiry scan (rule)",
                    "Ownership structure change detection (rule)",
                    "Screening data staleness check (rule)",
                    "Activity volume comparison (rule)",
                    "Outstanding alert aggregation (rule)",
                    "Regulatory requirement completeness (rule)",
                    "Review priority scoring (hybrid)",
                    "Review package assembly (hybrid)",
                ])
            ),
            (
                7, "Adverse Media & PEP Monitoring Agent", "📡", "Monitoring",
                "Monitoring interpreter with AI narrative. Retrieves new media/PEP/sanctions signals, "
                "deduplicates, scores severity, resolves entities; AI generates narrative summary and "
                "disposition. Degraded mode when no screening baseline exists.",
                1, json.dumps([
                    "New adverse media retrieval (rule)",
                    "PEP status change detection (rule)",
                    "Sanctions list update check (rule)",
                    "Media source credibility scoring (rule)",
                    "Alert deduplication (rule)",
                    "Historical media comparison (rule)",
                    "Media severity assessment (hybrid)",
                    "PEP proximity scoring (hybrid)",
                    "Entity resolution for media hits (hybrid)",
                    "Combined risk signal aggregation (hybrid)",
                    "Media narrative summarisation (ai)",
                    "Monitoring alert disposition (ai)",
                ])
            ),
            (
                8, "Behaviour & Risk Drift Agent", "📈", "Monitoring",
                "Rule-based drift detection with hybrid scoring. Compares transaction volume, geographic "
                "activity, counterparty concentration, product usage against onboarding baseline; scores "
                "velocity anomalies and peer deviation. Degraded mode when no transaction data available.",
                1, json.dumps([
                    "Transaction volume baseline comparison (rule)",
                    "Geographic activity deviation (rule)",
                    "Counterparty concentration check (rule)",
                    "Product usage deviation (rule)",
                    "Dormancy/reactivation detection (rule)",
                    "Threshold breach detection (rule)",
                    "Velocity anomaly scoring (hybrid)",
                    "Peer group deviation analysis (hybrid)",
                    "Temporal pattern drift detection (hybrid)",
                    "Multi-dimensional risk drift scoring (hybrid)",
                    "Drift narrative and recommendation (hybrid)",
                ])
            ),
            (
                9, "Regulatory Impact Agent", "⚖️", "Monitoring",
                "Detects when regulatory changes affect existing clients, "
                "tracks jurisdiction-specific regulations, and alerts on compliance requirement updates.",
                1, json.dumps([
                    "Regulatory change monitoring", "Impact assessment on client portfolio",
                    "Jurisdiction-specific regulation tracking", "Compliance requirement updates",
                    "Client-specific regulatory alerts"
                ])
            ),
            (
                10, "Ongoing Compliance Review Agent", "📋", "Monitoring",
                "Consolidation agent with AI narrative. Verifies document currency, screening recency, "
                "policy applicability, condition compliance, filing deadlines; consolidates inter-agent "
                "findings; AI generates compliance narrative and escalation/closure recommendation. "
                "Degraded mode when upstream agents have not run.",
                1, json.dumps([
                    "Document currency verification (rule)",
                    "Screening recency check (rule)",
                    "Policy change applicability check (rule)",
                    "Condition compliance tracking (rule)",
                    "Filing deadline monitoring (rule)",
                    "Inter-agent finding consolidation (rule)",
                    "Remediation tracker status (rule)",
                    "Compliance risk re-scoring (hybrid)",
                    "Review frequency recommendation (hybrid)",
                    "Compliance narrative generation (ai)",
                    "Escalation/closure recommendation (ai)",
                ])
            )
        ]
    
        # Supervisor agent type mapping: agent_number → supervisor schema AgentType
        supervisor_type_map = {
            1: "identity_document_integrity",
            2: "external_database_verification",
            3: "fincrime_screening",
            4: "corporate_structure_ubo",
            5: "compliance_memo_risk",
            6: "periodic_review_preparation",
            7: "adverse_media_pep_monitoring",
            8: "behaviour_risk_drift",
            9: "regulatory_impact",
            10: "ongoing_compliance_review",
        }
    
        # Improvement 5: Agent-to-Risk-Dimension mapping
        # D1=Customer/Entity, D2=Geographic, D3=Product/Service, D4=Channel, D5=Transaction
        risk_dimension_map = {
            1: json.dumps(["D1"]),
            2: json.dumps(["D1", "D2"]),
            3: json.dumps(["D1"]),
            4: json.dumps(["D1"]),
            5: json.dumps(["D1", "D2", "D3", "D4", "D5"]),
            6: json.dumps(["D1"]),
            7: json.dumps(["D1"]),
            8: json.dumps(["D1", "D5"]),
            9: json.dumps(["D2", "D3"]),
            10: json.dumps(["D1", "D2", "D3", "D4", "D5"]),
        }

        for agent_data in agents_seed:
            try:
                agent_num = agent_data[0]
                sv_type = supervisor_type_map.get(agent_num)
                risk_dims = risk_dimension_map.get(agent_num, json.dumps([]))
                # Convert enabled flag: 1 -> True for PostgreSQL boolean compatibility
                converted = list(agent_data)
                converted[5] = bool(converted[5])  # enabled field
                db.execute(
                    "INSERT INTO ai_agents (agent_number, name, icon, stage, description, enabled, checks, supervisor_agent_type, risk_dimensions) VALUES (?,?,?,?,?,?,?,?,?)",
                    tuple(converted) + (sv_type, risk_dims)
                )
            except Exception as e:
                logger.error(f"Failed to seed agent {agent_data[0]} '{agent_data[1]}': {e}", exc_info=True)
                try:
                    db.conn.rollback()
                except Exception:
                    pass
        db.commit()
        logger.info("AI agents seeded")

    # === AI CHECKS ===
    # Derived from verification_matrix.build_ai_checks_seed() — the single canonical source.
    # _SUPPLEMENTARY_AI_CHECKS_SEED handles doc types not yet codified in the matrix.
    if checks_count == 0:
        from verification_matrix import build_ai_checks_seed
        ai_checks_seed = build_ai_checks_seed() + _SUPPLEMENTARY_AI_CHECKS_SEED

        for check_data in ai_checks_seed:
            db.execute(
                "INSERT INTO ai_checks (category, doc_type, doc_name, checks) VALUES (?,?,?,?)",
                check_data
            )

        # Auto-update Agent 1 checks from ai_checks seed
        all_check_labels = []
        for check_data in ai_checks_seed:
            checks_list = json.loads(check_data[3])
            doc_name = check_data[2]
            for ch in checks_list:
                all_check_labels.append(f"{doc_name}: {ch['label']}")
        db.execute(
            "UPDATE ai_agents SET checks=? WHERE agent_number=1",
            (json.dumps(all_check_labels),)
        )
        db.commit()
        logger.info("AI checks seeded")

    db.commit()

    _seed_monitoring_demo_data(db)
    _ensure_document_health_monitor_agent(db)

    # Sprint 3: Seed default GDPR data retention policies
    # (extracted to _ensure_retention_policies — PR-31 — so it also runs on the
    # already-seeded early-return path above, which staging always takes)
    _ensure_retention_policies(db)

    db.commit()
    logger.info("Database seeded with initial data")


# ── Supplementary AI checks for doc types not yet in verification_matrix.ALL_DOC_CHECKS ──
# These doc types (contracts, aml_policy, source_wealth, source_funds, bank_statements)
# are handled by the layered engine but their register entries are not yet codified in
# verification_matrix.py. They live here until promoted to the matrix.
_SUPPLEMENTARY_AI_CHECKS_SEED = [
    ("entity", "cert_reg", "Certificate of Registration (Retired)", json.dumps([])),
    ("entity", "contracts", "Client/Supplier Contracts", json.dumps([
        {"id": "DOC-36", "label": "Name Match", "type": "name", "classification": "rule",
         "rule": "Entity name must appear in the contract. PASS if name present and matches. WARN if partial match. FAIL if not present."},
        {"id": "DOC-37", "label": "Relevance", "type": "content", "classification": "hybrid",
         "rule": "Contract must be relevant to the declared business activity. PASS if relevant. WARN if tangentially related. FAIL if unrelated."},
        {"id": "DOC-38", "label": "Clarity", "type": "quality", "classification": "rule",
         "rule": "Document must be legible. PASS if legible. WARN if partially legible. FAIL if illegible."},
    ])),
    ("entity", "aml_policy", "AML/CFT Policy", json.dumps([
        {"id": "DOC-39", "label": "Completeness", "type": "content", "classification": "hybrid",
         "rule": "Must cover key AML areas (CDD, sanctions screening, reporting). PASS if all key areas covered. WARN if minor gaps. FAIL if major areas missing."},
        {"id": "DOC-40", "label": "Date", "type": "age", "classification": "rule",
         "rule": "Policy must be dated and reviewed within last 12 months. PASS if within 12 months. WARN if 12-24 months. FAIL if older or undated."},
        {"id": "DOC-41", "label": "Relevance", "type": "content", "classification": "hybrid",
         "rule": "Must be relevant to the entity's business activities. PASS if relevant. WARN if generic. FAIL if irrelevant."},
    ])),
    ("entity", "source_wealth", "Source of Wealth Documentation", json.dumps([
        {"id": "DOC-42", "label": "Consistency", "type": "content", "classification": "hybrid",
         "rule": "Must be consistent with declared source of wealth in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration."},
        {"id": "DOC-43", "label": "Clarity", "type": "quality", "classification": "rule",
         "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible."},
    ])),
    ("entity", "source_funds", "Source of Funds Documentation", json.dumps([
        {"id": "DOC-44", "label": "Consistency", "type": "content", "classification": "hybrid",
         "rule": "Must be consistent with declared source of funds in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration."},
        {"id": "DOC-45", "label": "Clarity", "type": "quality", "classification": "rule",
         "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible."},
    ])),
    ("entity", "bank_statements", "Bank Statements", json.dumps([
        {"id": "DOC-46", "label": "Period", "type": "age", "classification": "rule",
         "rule": "Must cover a recent period (within last 6 months). PASS if within 6 months. WARN if 6-12 months. FAIL if older than 12 months."},
        {"id": "DOC-47", "label": "Name Match", "type": "name", "classification": "rule",
         "rule": "Account holder name must match the declared entity or person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
        {"id": "DOC-74", "label": "Completeness", "type": "quality", "classification": "rule",
         "rule": "All pages must be present. PASS if complete. WARN if minor pages missing. FAIL if key pages missing."},
    ])),
]


def normalize_legacy_doc_types(db: DBConnection):
    """
    Idempotent migration: normalize legacy portal-style doc_type values in the
    documents table to canonical backend values.

    Runs on every startup. Only updates rows whose doc_type matches a known
    legacy key; already-canonical rows are untouched.
    """
    _DOC_TYPE_NORMALIZE = {
        "doc-coi": "cert_inc", "certificate_of_incorporation": "cert_inc",
        "certificate of incorporation": "cert_inc", "certificate-incorporation": "cert_inc",
        "incorporation_certificate": "cert_inc", "incorporation certificate": "cert_inc",
        "doc-memarts": "memarts", "memorandum_of_association": "memarts",
        "memorandum of association": "memarts", "memorandum_and_articles": "memarts",
        "memorandum and articles": "memarts", "articles_of_association": "memarts",
        "articles of association": "memarts", "doc-shareholders": "reg_sh",
        "register_of_shareholders": "reg_sh", "register of shareholders": "reg_sh",
        "shareholder_register": "reg_sh", "shareholder register": "reg_sh",
        "doc-directors-reg": "reg_dir", "doc-financials": "fin_stmt", "doc-proof-address": "poa",
        "register_of_directors": "reg_dir", "register of directors": "reg_dir",
        "director_register": "reg_dir", "director register": "reg_dir",
        "proof_of_address": "poa", "proof of address": "poa", "address_proof": "poa",
        "financial_statements": "fin_stmt", "financial statements": "fin_stmt",
        "doc-board-res": "board_res", "doc-structure-chart": "structure_chart",
        "board_resolution": "board_res", "board resolution": "board_res",
        "structure chart": "structure_chart", "ownership_structure_chart": "structure_chart",
        "doc-bank-ref": "bankref", "doc-license-cert": "licence",
        "bank_reference": "bankref", "bank reference": "bankref",
        "license": "licence", "licence_certificate": "licence", "license_certificate": "licence",
        "doc-contracts": "contracts", "doc-source-wealth-proof": "source_wealth",
        "doc-source-funds-proof": "source_funds", "doc-bank-statements": "bank_statements",
        "source_of_wealth": "source_wealth", "source of wealth": "source_wealth",
        "source_of_funds": "source_funds", "source of funds": "source_funds",
        "bank statements": "bank_statements",
        "doc-aml-policy": "aml_policy",
        "aml policy": "aml_policy",
        "id_card": "national_id",
        "identity_card": "national_id",
        "drivers_license": "national_id",
        "driver_license": "national_id",
        "driving_license": "national_id",
        "director_id": "national_id",
        "ubo_id": "national_id",
        "pep-declaration": "pep_declaration",
    }
    total_updated = 0
    for old_type, new_type in _DOC_TYPE_NORMALIZE.items():
        try:
            db.execute(
                "UPDATE documents SET doc_type=? WHERE LOWER(doc_type)=LOWER(?)",
                (new_type, old_type)
            )
            # rowcount is not always reliable across DB adapters, so we count separately
            count_row = db.execute(
                "SELECT COUNT(*) as cnt FROM documents WHERE LOWER(doc_type)=LOWER(?)",
                (old_type,)
            ).fetchone()
            # If the count is 0 after update, the update worked (or there were none)
        except Exception as e:
            logger.warning(f"normalize_legacy_doc_types: failed to update {old_type} -> {new_type}: {e}")
    # Log summary
    remaining = 0
    for old_type in _DOC_TYPE_NORMALIZE:
        try:
            row = db.execute("SELECT COUNT(*) as cnt FROM documents WHERE LOWER(doc_type)=LOWER(?)", (old_type,)).fetchone()
            remaining += (row["cnt"] if row else 0)
        except Exception:
            pass
    db.commit()
    if remaining == 0:
        logger.info("normalize_legacy_doc_types: all document types are canonical (no legacy values remaining)")
    else:
        logger.warning(f"normalize_legacy_doc_types: {remaining} legacy doc_type rows still remain after migration")


def sync_ai_checks_from_seed(db: DBConnection):
    """
    Upsert the canonical ai_checks seed on every startup.

    Runs unconditionally so that stale rows on existing databases (staging, prod)
    are always brought in line with the current source of truth.  Back-office
    manual edits to individual checks are intentionally overwritten here because
    the verification_matrix.py / db.py seed IS the source of truth; any
    operator customisation should be re-applied via the UI after a deploy.
    """
    from verification_matrix import build_ai_checks_seed
    ai_checks_seed = build_ai_checks_seed() + _SUPPLEMENTARY_AI_CHECKS_SEED

    updated = 0
    inserted = 0
    for category, doc_type, doc_name, checks_json in ai_checks_seed:
        existing = db.execute(
            "SELECT id FROM ai_checks WHERE doc_type=? AND category=?",
            (doc_type, category)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE ai_checks SET doc_name=?, checks=?, updated_at=datetime('now') WHERE doc_type=? AND category=?",
                (doc_name, checks_json, doc_type, category)
            )
            updated += 1
        else:
            db.execute(
                "INSERT INTO ai_checks (category, doc_type, doc_name, checks) VALUES (?,?,?,?)",
                (category, doc_type, doc_name, checks_json)
            )
            inserted += 1

    # Commit check updates BEFORE agent rebuild so they are saved even if rebuild fails.
    # (PostgreSQL JSONB columns return already-parsed Python objects, not JSON strings —
    # committing here prevents the agent-rebuild step from rolling back the check updates.)
    db.commit()
    logger.info(f"ai_checks sync complete: {updated} updated, {inserted} inserted")

    # Rebuild Agent 1 checks list from updated ai_checks
    try:
        all_rows = db.execute("SELECT doc_name, checks FROM ai_checks ORDER BY category, id").fetchall()
        all_check_labels = []
        for row in all_rows:
            raw = row["checks"]
            # PostgreSQL JSONB returns a Python list/dict; SQLite TEXT returns a JSON string.
            if isinstance(raw, (list, dict)):
                checks_list = raw if isinstance(raw, list) else []
            else:
                checks_list = json.loads(raw) if raw else []
            for ch in checks_list:
                if isinstance(ch, dict) and ch.get("label"):
                    all_check_labels.append(f"{row['doc_name']}: {ch['label']}")
        db.execute(
            "UPDATE ai_agents SET checks=? WHERE agent_number=1",
            (json.dumps(all_check_labels),)
        )
        db.commit()
        logger.info(f"Agent 1 checks list rebuilt: {len(all_check_labels)} checks")
    except Exception as e:
        logger.error(f"Agent 1 checks rebuild failed (check data already committed): {e}", exc_info=True)


# ============================================================================
# Migration Function
# ============================================================================

def migrate_sqlite_to_postgres(sqlite_path: str, pg_url: str):
    """
    Migrate all data from SQLite database to PostgreSQL.
    Preserves all records and IDs.
    """
    import psycopg2

    logger.info(f"Starting migration from SQLite ({sqlite_path}) to PostgreSQL")

    # Connect to SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    # Connect to PostgreSQL
    pg_conn = psycopg2.connect(pg_url)
    pg_cursor = pg_conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Get all table names from SQLite
        sqlite_cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in sqlite_cursor.fetchall()]

        for table in tables:
            logger.info(f"Migrating table: {table}")

            # Get column names and data from SQLite
            sqlite_cursor.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in sqlite_cursor.fetchall()]

            sqlite_cursor.execute(f"SELECT * FROM {table}")
            rows = sqlite_cursor.fetchall()

            if not rows:
                logger.info(f"  No rows to migrate for {table}")
                continue

            # Prepare insert statement
            placeholders = ", ".join(["%s"] * len(columns))
            insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

            # Insert rows into PostgreSQL.  Each row runs under a SAVEPOINT
            # (P12-5 review M3): a plain rollback() here used to discard EVERY
            # previously-inserted row of the table and then claim
            # "Migrated N rows" — with enum CHECK constraints now installed, a
            # single off-canon legacy row would have silently destroyed the
            # whole table's migration.
            inserted = 0
            failed = 0
            for row in rows:
                values = tuple(row)
                try:
                    pg_cursor.execute("SAVEPOINT migrate_row")
                    pg_cursor.execute(insert_sql, values)
                    pg_cursor.execute("RELEASE SAVEPOINT migrate_row")
                    inserted += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"  Error inserting row into {table}: {e}")
                    pg_cursor.execute("ROLLBACK TO SAVEPOINT migrate_row")

            pg_conn.commit()
            if failed:
                logger.error(
                    f"  Migrated {inserted} of {len(rows)} rows for {table} — "
                    f"{failed} row(s) FAILED and were NOT migrated; review the "
                    f"errors above before relying on this migration"
                )
            else:
                logger.info(f"  Migrated {inserted} rows")

            # Verify destination row count against the source (best effort).
            try:
                pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
                dest_count = pg_cursor.fetchone()
                dest_count = list(dest_count.values())[0] if isinstance(dest_count, dict) else dest_count[0]
                if int(dest_count) < len(rows):
                    logger.error(
                        f"  ROW COUNT MISMATCH for {table}: source={len(rows)} "
                        f"destination={dest_count}"
                    )
            except Exception as count_err:
                logger.warning(f"  Could not verify row count for {table}: {count_err}")

        logger.info("Migration completed successfully")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        pg_conn.rollback()
        raise
    finally:
        sqlite_cursor.close()
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()


# ============================================================================
# Backup & Restore Functions
# ============================================================================

def backup_database(backup_dir: str = "./backups") -> str:
    """
    Create a timestamped PostgreSQL backup using pg_dump.
    Returns the path to the backup file.
    """
    if not USE_POSTGRESQL:
        raise ValueError("Backup only supported for PostgreSQL")

    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = Path(backup_dir) / f"arie_finance_{timestamp}.sql"

    logger.info(f"Creating database backup: {backup_file}")

    try:
        result = subprocess.run(
            ["pg_dump", DATABASE_URL, "-f", str(backup_file)],
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Backup created successfully: {backup_file}")
        return str(backup_file)
    except subprocess.CalledProcessError as e:
        logger.error(f"Backup failed: {e.stderr}")
        raise


def restore_database(backup_file: str) -> None:
    """
    Restore PostgreSQL database from a backup file using pg_restore or psql.
    """
    if not USE_POSTGRESQL:
        raise ValueError("Restore only supported for PostgreSQL")

    if not Path(backup_file).exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    logger.info(f"Restoring database from backup: {backup_file}")

    try:
        # Use psql for .sql files, pg_restore for .dump files
        if backup_file.endswith('.sql'):
            result = subprocess.run(
                ["psql", DATABASE_URL, "-f", backup_file],
                capture_output=True,
                text=True,
                check=True
            )
        else:
            result = subprocess.run(
                ["pg_restore", "-d", DATABASE_URL, backup_file],
                capture_output=True,
                text=True,
                check=True
            )
        logger.info("Database restored successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Restore failed: {e.stderr}")
        raise


def list_backups(backup_dir: str = "./backups") -> List[Dict[str, Any]]:
    """
    List all available backups with timestamps and file sizes.
    """
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        return []

    backups = []
    for backup_file in sorted(backup_path.glob("arie_finance_*.sql"), reverse=True):
        stat = backup_file.stat()
        backups.append({
            "filename": backup_file.name,
            "path": str(backup_file),
            "size_bytes": stat.st_size,
            "size_mb": stat.st_size / (1024 * 1024),
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
        })

    return backups


# ============================================================================
# Cleanup
# ============================================================================

def close_db():
    """Close database connections and cleanup resources."""
    close_pg_pool()
    logger.info("Database connections closed")
