"""
Database abstraction layer for ARIE Finance platform.
Supports both SQLite (development) and PostgreSQL (production).
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple
import secrets
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import psycopg2 for PostgreSQL support
try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


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

def init_pg_pool():
    """Initialize PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is None and USE_POSTGRESQL:
        if not PSYCOPG2_AVAILABLE:
            raise ImportError(
                "psycopg2-binary is required for PostgreSQL support. "
                "Install it with: pip install psycopg2-binary --break-system-packages"
            )
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            1, 5,
            DATABASE_URL,
            sslmode='require'
        )
        logger.info("PostgreSQL connection pool initialized (minconn=1, maxconn=5)")


def close_pg_pool():
    """Close PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.closeall()
        _pg_pool = None
        logger.info("PostgreSQL connection pool closed")


# ============================================================================
# Connection Wrapper Classes
# ============================================================================

class DBConnection:
    """
    Database connection wrapper that abstracts SQL dialect differences.
    Handles placeholder translation (? for SQLite, %s for PostgreSQL).
    """

    def __init__(self, conn, is_postgres: bool = False):
        self.conn = conn
        self.is_postgres = is_postgres
        self._cursor = None

    def _translate_query(self, sql: str) -> str:
        """
        Translate SQLite-specific SQL syntax to PostgreSQL equivalents.
        Handles: placeholders, datetime functions, INSERT OR variants, boolean literals.
        """
        if not self.is_postgres:
            return sql
        # 1. Placeholders: ? -> %s
        sql = sql.replace('?', '%s')
        # 2. Datetime: datetime('now') -> NOW()
        sql = sql.replace("datetime('now')", "NOW()")
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
        """Execute multiple SQL statements. Handles dialect differences."""
        if self.is_postgres:
            # For PostgreSQL, execute the entire script as one block.
            # Schema DDL uses PostgreSQL-native syntax already (_get_postgres_schema),
            # so no per-statement translation is needed here.
            cursor = self._cursor_or_create()
            cursor.execute(sql)
        else:
            self.conn.executescript(sql)

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

    def commit(self) -> None:
        """Commit transaction."""
        self.conn.commit()

    def close(self) -> None:
        """Close connection and return to pool if PostgreSQL."""
        if self._cursor:
            self._cursor.close()
        if self.is_postgres:
            # Return connection to pool
            if _pg_pool:
                _pg_pool.putconn(self.conn)
        else:
            self.conn.close()


# ============================================================================
# Main Database Interface
# ============================================================================

def get_db() -> DBConnection:
    """
    Get a database connection.
    - For PostgreSQL: returns a connection from the pool
    - For SQLite: returns a new connection

    C-07: SQLite is BLOCKED in production. Production MUST use PostgreSQL.
    """
    env = _CFG_ENVIRONMENT.lower()

    if USE_POSTGRESQL:
        init_pg_pool()
        conn = _pg_pool.getconn()
        return DBConnection(conn, is_postgres=True)
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
        return DBConnection(conn, is_postgres=False)


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
        status TEXT DEFAULT 'active',
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
            'kyc_documents','kyc_submitted','compliance_review','in_review',
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Intermediary shareholders
    CREATE TABLE IF NOT EXISTS intermediaries (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        entity_name TEXT NOT NULL,
        jurisdiction TEXT,
        ownership_pct REAL,
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
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','flagged','failed')),
        verification_results JSONB DEFAULT '{}',
        review_status TEXT DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','info_requested')),
        review_comment TEXT,
        reviewed_by TEXT REFERENCES users(id),
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        verified_at TIMESTAMP,
        reviewed_at TIMESTAMP
    );

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
        status TEXT NOT NULL,
        checks_json JSONB,
        flags_json JSONB,
        requires_review BOOLEAN DEFAULT false,
        source TEXT DEFAULT 'ai',
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
        reviewer_id TEXT REFERENCES users(id),
        reviewer_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(application_id, subject_type, subject_name)
    );

    -- Audit Trail
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_id TEXT,
        user_name TEXT,
        user_role TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        ip_address TEXT
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
        application_id TEXT REFERENCES applications(id),
        form_data JSONB DEFAULT '{}',
        last_step INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Monitoring Alerts
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        id SERIAL PRIMARY KEY,
        application_id TEXT REFERENCES applications(id),
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
        reviewed_by TEXT REFERENCES users(id)
    );

    -- Periodic Reviews
    CREATE TABLE IF NOT EXISTS periodic_reviews (
        id SERIAL PRIMARY KEY,
        application_id TEXT REFERENCES applications(id),
        client_name TEXT,
        risk_level TEXT,
        trigger_type TEXT,
        trigger_reason TEXT,
        previous_risk_level TEXT,
        new_risk_level TEXT,
        review_memo TEXT,
        status TEXT DEFAULT 'pending',
        due_date DATE,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

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

    -- Client Notifications
    CREATE TABLE IF NOT EXISTS client_notifications (
        id SERIAL PRIMARY KEY,
        application_id TEXT REFERENCES applications(id),
        client_id TEXT REFERENCES clients(id),
        notification_type TEXT,
        title TEXT NOT NULL,
        message TEXT,
        documents_list TEXT,
        read_status BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        read_at TIMESTAMP
    );

    -- Suspicious Activity Reports (SAR)
    CREATE TABLE IF NOT EXISTS sar_reports (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT REFERENCES applications(id),
        alert_id INTEGER REFERENCES monitoring_alerts(id),
        sar_reference TEXT UNIQUE,
        report_type TEXT DEFAULT 'SAR' CHECK(report_type IN ('SAR','STR','CTR','MLRO')),
        subject_name TEXT NOT NULL,
        subject_type TEXT DEFAULT 'individual' CHECK(subject_type IN ('individual','entity')),
        risk_level TEXT,
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

    -- Enhanced Due Diligence (EDD) Cases
    CREATE TABLE IF NOT EXISTS edd_cases (
        id SERIAL PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id),
        client_name TEXT NOT NULL,
        risk_level TEXT,
        risk_score REAL,
        stage TEXT DEFAULT 'triggered' CHECK(stage IN ('triggered','information_gathering','analysis','pending_senior_review','edd_approved','edd_rejected')),
        assigned_officer TEXT REFERENCES users(id),
        senior_reviewer TEXT REFERENCES users(id),
        trigger_source TEXT DEFAULT 'officer_decision',
        trigger_notes TEXT,
        edd_notes JSONB DEFAULT '[]',
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        decided_at TIMESTAMP,
        triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

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
        supervisor_status TEXT DEFAULT 'pending',
        supervisor_summary TEXT,
        supervisor_contradictions TEXT DEFAULT '[]',
        rule_violations TEXT DEFAULT '[]',
        rule_engine_status TEXT DEFAULT 'pending',
        blocked BOOLEAN DEFAULT FALSE,
        block_reason TEXT,
        pdf_generated_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

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
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_application_id ON periodic_reviews(application_id);
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
        purged_by TEXT REFERENCES users(id),
        purged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_dsr_status ON data_subject_requests(status);
    CREATE INDEX IF NOT EXISTS idx_dsr_client ON data_subject_requests(client_id);
    CREATE INDEX IF NOT EXISTS idx_purge_log_category ON data_purge_log(data_category);

    -- Rate limiting persistence (survives restarts for auth-critical keys)
    CREATE TABLE IF NOT EXISTS rate_limits (
        id SERIAL PRIMARY KEY,
        key TEXT NOT NULL,
        attempted_at DOUBLE PRECISION NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rate_limits_key ON rate_limits(key);
    CREATE INDEX IF NOT EXISTS idx_rate_limits_attempted ON rate_limits(attempted_at);

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

    -- Supervisor audit log (production-grade, uses shared DB)
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
        status TEXT DEFAULT 'active',
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
            'kyc_documents','kyc_submitted','compliance_review','in_review',
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
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
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
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Intermediary shareholders
    CREATE TABLE IF NOT EXISTS intermediaries (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_key TEXT,
        entity_name TEXT NOT NULL,
        jurisdiction TEXT,
        ownership_pct REAL,
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
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','flagged','failed')),
        verification_results TEXT DEFAULT '{}',
        review_status TEXT DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','info_requested')),
        review_comment TEXT,
        reviewed_by TEXT REFERENCES users(id),
        uploaded_at TEXT DEFAULT (datetime('now')),
        verified_at TEXT,
        reviewed_at TEXT
    );

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
        status TEXT NOT NULL,
        checks_json TEXT,
        flags_json TEXT,
        requires_review INTEGER DEFAULT 0,
        source TEXT DEFAULT 'ai',
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
        reviewer_id TEXT REFERENCES users(id),
        reviewer_name TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(application_id, subject_type, subject_name)
    );

    -- Audit Trail
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT (datetime('now')),
        user_id TEXT,
        user_name TEXT,
        user_role TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        ip_address TEXT
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
        application_id TEXT REFERENCES applications(id),
        form_data TEXT DEFAULT '{}',
        last_step INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Monitoring Alerts
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id),
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
        reviewed_by TEXT REFERENCES users(id)
    );

    -- Periodic Reviews
    CREATE TABLE IF NOT EXISTS periodic_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id),
        client_name TEXT,
        risk_level TEXT,
        trigger_type TEXT,
        trigger_reason TEXT,
        previous_risk_level TEXT,
        new_risk_level TEXT,
        review_memo TEXT,
        status TEXT DEFAULT 'pending',
        due_date TEXT,
        started_at TEXT,
        completed_at TEXT,
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now'))
    );

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

    -- Client Notifications
    CREATE TABLE IF NOT EXISTS client_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id),
        client_id TEXT REFERENCES clients(id),
        notification_type TEXT,
        title TEXT NOT NULL,
        message TEXT,
        documents_list TEXT,
        read_status INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        read_at TEXT
    );

    -- Suspicious Activity Reports (SAR)
    CREATE TABLE IF NOT EXISTS sar_reports (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT REFERENCES applications(id),
        alert_id INTEGER REFERENCES monitoring_alerts(id),
        sar_reference TEXT UNIQUE,
        report_type TEXT DEFAULT 'SAR' CHECK(report_type IN ('SAR','STR','CTR','MLRO')),
        subject_name TEXT NOT NULL,
        subject_type TEXT DEFAULT 'individual' CHECK(subject_type IN ('individual','entity')),
        risk_level TEXT,
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

    -- Enhanced Due Diligence (EDD) Cases
    CREATE TABLE IF NOT EXISTS edd_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id),
        client_name TEXT NOT NULL,
        risk_level TEXT,
        risk_score REAL,
        stage TEXT DEFAULT 'triggered' CHECK(stage IN ('triggered','information_gathering','analysis','pending_senior_review','edd_approved','edd_rejected')),
        assigned_officer TEXT REFERENCES users(id),
        senior_reviewer TEXT REFERENCES users(id),
        trigger_source TEXT DEFAULT 'officer_decision',
        trigger_notes TEXT,
        edd_notes TEXT DEFAULT '[]',
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        decided_at TEXT,
        triggered_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

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
        supervisor_status TEXT DEFAULT 'pending',
        supervisor_summary TEXT,
        supervisor_contradictions TEXT DEFAULT '[]',
        rule_violations TEXT DEFAULT '[]',
        rule_engine_status TEXT DEFAULT 'pending',
        blocked INTEGER DEFAULT 0,
        block_reason TEXT,
        pdf_generated_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

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
        purged_by TEXT REFERENCES users(id),
        purged_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_dsr_status ON data_subject_requests(status);
    CREATE INDEX IF NOT EXISTS idx_dsr_client ON data_subject_requests(client_id);
    CREATE INDEX IF NOT EXISTS idx_purge_log_category ON data_purge_log(data_category);

    -- Rate limiting persistence (survives restarts for auth-critical keys)
    CREATE TABLE IF NOT EXISTS rate_limits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        attempted_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rate_limits_key ON rate_limits(key);
    CREATE INDEX IF NOT EXISTS idx_rate_limits_attempted ON rate_limits(attempted_at);

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

    -- Supervisor audit log (production-grade, uses shared DB)
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
    try:
        db = get_db()
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
                json.dumps(checks) if checks else None,
                json.dumps(flags) if flags else None,
                1 if requires_review else 0,
                source,
                started_at or datetime.now().isoformat(),
                error_message,
            )
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.debug(f"Could not log agent execution: {e}")


def init_db():
    """Initialize database schema (creates tables if they don't exist)."""
    db = get_db()
    try:
        if USE_POSTGRESQL:
            schema = _get_postgres_schema()
        else:
            schema = _get_sqlite_schema()
        db.executescript(schema)
        db.commit()
        logger.info("Database schema initialized")

        # ── Migration: Add pre-approval columns if missing (v2.1) ──
        _run_migrations(db)
        db.commit()

        # Ensure built-in resources exist for the back-office reference library.
        _ensure_default_compliance_resources(db)
        db.commit()

        # Ensure system settings row exists for configuration-backed settings.
        _ensure_default_system_settings(db)
        db.commit()

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

        # ── Fix: Add 'submitted' to applications status CHECK constraint (PostgreSQL only) ──
        if USE_POSTGRESQL:
            try:
                db.execute("ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_status_check")
                db.execute("""ALTER TABLE applications ADD CONSTRAINT applications_status_check
                    CHECK(status IN ('draft','submitted','prescreening_submitted','pricing_review','pricing_accepted',
                    'pre_approval_review','pre_approved','kyc_documents','kyc_submitted','compliance_review','in_review',
                    'edd_required','approved','rejected','rmi_sent','withdrawn'))""")
                db.commit()
            except Exception as e:
                logger.debug(f"Status constraint update: {e}")
                try:
                    db.conn.rollback()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise
    finally:
        db.close()


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


def _run_migrations(db: DBConnection):
    """Run incremental schema migrations for existing databases."""
    # Check if pre_approval columns exist on applications table
    try:
        db.execute("SELECT pre_approval_decision FROM applications LIMIT 1")
    except Exception:
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
    try:
        db.execute("SELECT password_reset_token FROM clients LIMIT 1")
    except Exception:
        logger.info("Migration: Adding password reset columns to clients table")
        for col in ["password_reset_token TEXT", "password_reset_expires TEXT"]:
            try:
                db.execute(f"ALTER TABLE clients ADD COLUMN {col}")
            except Exception as e:
                logger.debug(f"Migration column may already exist: {e}")
        logger.info("Migration: Password reset columns added")

    # Migration v2.2: Add scoring config columns to risk_config
    try:
        db.execute("SELECT country_risk_scores FROM risk_config LIMIT 1")
    except Exception:
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
    try:
        db.execute("SELECT s3_key FROM documents LIMIT 1")
    except Exception:
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
            try:
                db.execute(f"SELECT {column_name} FROM {table_name} LIMIT 1")
            except Exception:
                logger.info("Migration v2.5: Adding %s.%s", table_name, column_name)
                try:
                    db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                except Exception as e:
                    logger.debug("Migration column %s.%s may already exist: %s", table_name, column_name, e)

    try:
        db.execute("SELECT person_key FROM intermediaries LIMIT 1")
    except Exception:
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
        try:
            db.execute(f"SELECT {column_name} FROM documents LIMIT 1")
        except Exception:
            logger.info("Migration v2.7: Adding documents.%s", column_name)
            try:
                db.execute(f"ALTER TABLE documents ADD COLUMN {column_name} {column_type}")
            except Exception as e:
                logger.debug("Migration documents.%s may already exist: %s", column_name, e)

    # Migration v2.4: Add compliance_resources table for back-office reference materials
    try:
        db.execute("SELECT slug FROM compliance_resources LIMIT 1")
    except Exception:
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
    try:
        db.execute("SELECT company_name FROM system_settings LIMIT 1")
    except Exception:
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

    # Migration v2.6: Add regulatory_documents table for regulatory intelligence workflow
    try:
        db.execute("SELECT regulator FROM regulatory_documents LIMIT 1")
    except Exception:
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
    try:
        db.execute("SELECT applicant_id FROM sumsub_applicant_mappings LIMIT 1")
    except Exception:
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
    try:
        db.execute("SELECT pipeline_id FROM supervisor_pipeline_results LIMIT 1")
    except Exception:
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

    try:
        db.execute("SELECT entry_hash FROM supervisor_audit_log LIMIT 1")
    except Exception:
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


def _populate_default_scoring_config(db: 'DBConnection'):
    """Populate default country/sector/entity scores for existing risk_config rows."""
    existing = db.execute("SELECT country_risk_scores FROM risk_config WHERE id=1").fetchone()
    if existing and existing["country_risk_scores"] and existing["country_risk_scores"] != '{}':
        return  # Already populated
    default_country = json.dumps({
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
        "estonia": 2, "pakistan": 2,
        "seychelles": 3,
        "algeria": 3, "burkina faso": 3, "cameroon": 3, "democratic republic of congo": 3,
        "haiti": 3, "kenya": 3, "laos": 3, "lebanon": 3, "mali": 3, "monaco": 3,
        "mozambique": 3, "nigeria": 3, "philippines": 3, "senegal": 3, "south africa": 3,
        "south sudan": 3, "tanzania": 3, "venezuela": 3, "vietnam": 3, "yemen": 3,
        "bermuda": 3, "vanuatu": 3, "samoa": 3, "marshall islands": 3, "iraq": 3,
        "iran": 4, "north korea": 4, "myanmar": 4, "russia": 4, "syria": 4, "belarus": 4,
        "cuba": 4, "crimea": 4, "afghanistan": 4, "somalia": 4, "libya": 4, "eritrea": 4, "sudan": 4,
        "bvi": 4, "british virgin islands": 4, "cayman islands": 4, "panama": 4
    })
    default_sector = json.dumps({
        "regulated financial": 1, "government": 1, "bank": 1, "listed company": 1,
        "agriculture": 1, "education": 1,
        "healthcare": 2, "technology": 2, "software": 2, "saas": 2, "manufacturing": 2,
        "retail": 2, "e-commerce": 2, "media": 2, "logistics": 2, "insurance": 2,
        "telecommunications": 2, "construction": 2, "banking": 2,
        "import": 3, "export": 3, "real estate": 3, "mining": 3,
        "oil": 3, "gas": 3, "energy": 3, "money services": 3, "forex": 3, "precious": 3,
        "non-profit": 3, "ngo": 3, "charity": 3, "advisory": 3,
        "management consulting": 3, "consulting": 3, "financial / tax advisory": 3,
        "fintech": 3, "e-money": 3, "legal": 3, "accounting": 3, "shipping": 3, "maritime": 3,
        "crypto": 4, "virtual asset": 4, "gambling": 4, "gaming": 4, "betting": 4,
        "arms": 4, "defence": 4, "military": 4, "shell company": 4, "nominee": 4,
        "precious metals": 4
    })
    default_entity = json.dumps({
        "listed company": 1, "regulated financial institution": 1, "regulated fi": 1,
        "regulated entity": 1, "government": 1, "government body": 1, "public sector": 1,
        "listed": 1, "regulated": 1,
        "large private company": 2, "large private": 2, "sme": 2, "private company": 2,
        "regulated fund": 2,
        "newly incorporated": 3, "trust": 3, "foundation": 3, "ngo": 3, "non-profit": 3,
        "unregulated fund": 4, "spv": 4, "shell company": 4, "shell": 4
    })
    db.execute(
        "UPDATE risk_config SET country_risk_scores=?, sector_risk_scores=?, entity_type_scores=? WHERE id=1",
        (default_country, default_sector, default_entity)
    )


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
            "Validates authenticity and consistency of uploaded documents against predefined deterministic checks. "
            "Each document type has a fixed set of checks defined in the rule engine — the AI evaluates each check but does NOT decide what checks to run. "
            "Covers entity documents (COI, M&A, registers, financials, etc.) and person documents (passport, PoA, CV, bank reference). "
            "Does NOT do sanctions screening or registry lookups."
        ),
        "checks": [
            "COI: Entity Name Match", "COI: Registration Number", "COI: Document Clarity",
            "M&A: Entity Name Match", "M&A: Completeness", "M&A: Certification",
            "Registration: Entity Name Match", "Registration: Current Validity", "Registration: Document Clarity",
            "Shareholder Register: Ownership Consistency", "Shareholder Register: Completeness", "Shareholder Register: Currency",
            "Director Register: Director Consistency", "Director Register: Completeness", "Director Register: Clarity",
            "Financials: Financial Period", "Financials: Entity Name Match", "Financials: Audit Status", "Financials: Completeness",
            "Board Resolution: Signatory Match", "Board Resolution: Date", "Board Resolution: Scope of Authority",
            "Structure Chart: UBO Chain", "Structure Chart: Ownership Match", "Structure Chart: Legibility",
            "Proof of Address: Document Date", "Proof of Address: Entity Name Match", "Proof of Address: Clarity", "Proof of Address: Address Match",
            "Bank Reference: Letterhead", "Bank Reference: Date", "Bank Reference: Entity Name Match",
            "Licence: Entity Name Match", "Licence: Validity", "Licence: Issuing Authority",
            "Passport: Document Expiry", "Passport: Photo Quality", "Passport: Name Match", "Passport: Nationality Match",
            "Personal PoA: Document Date", "Personal PoA: Name Match", "Personal PoA: Clarity", "Personal PoA: Certification",
            "CV: Name Match", "CV: Employment History",
            "Bank Reference (PEP): Date", "Bank Reference (PEP): Name Match", "Bank Reference (PEP): Bank ID",
            "Bank Reference (PEP): Account Standing", "Bank Reference (PEP): Signatory",
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
        # Still check if monitoring demo data needs seeding (added post-initial-seed)
        _seed_monitoring_demo_data(db)
        return

    logger.info(f"Seed status: users={users_count}, agents={agents_count}, checks={checks_count}, risk={risk_count}")

    # === USERS ===
    if users_count == 0:
        init_password = _CFG_ADMIN_INITIAL_PASSWORD
        if not init_password:
            init_password = secrets.token_urlsafe(16)
            print(f"\n  ⚠️  INITIAL ADMIN PASSWORD (save this now): {init_password}")
            print(f"  ⚠️  Change it immediately after first login.\n")

        pw_hash = bcrypt.hashpw(init_password.encode(), bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("admin001", "asudally@onboarda.com", pw_hash, "Aisha Sudally", "admin", "active")
        )
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("sco001", "raj.patel@onboarda.com", pw_hash, "Raj Patel", "sco", "active")
        )
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("co001", "m.dubois@onboarda.com", pw_hash, "Marie Dubois", "co", "active")
        )
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("analyst001", "l.wei@onboarda.com", pw_hash, "Li Wei", "analyst", "active")
        )
        db.commit()
        logger.info("Users seeded")

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
        {"level": "LOW", "min": 0, "max": 29.9},
        {"level": "MEDIUM", "min": 30, "max": 49.9},
        {"level": "HIGH", "min": 50, "max": 69.9},
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
        "estonia": 2, "pakistan": 2,
        # Score 3 — High Risk (FATF grey list, offshore/secrecy)
        "seychelles": 3,
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
        "telecommunications": 2, "construction": 2, "banking": 2,
        "import": 3, "export": 3, "real estate": 3, "mining": 3,
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
        # Entity document checks (aligned with ENTITY_DOC_CHECKS in back office)
        "COI: Entity Name Match", "COI: Registration Number", "COI: Document Clarity",
        "M&A: Entity Name Match", "M&A: Completeness", "M&A: Certification",
        "Registration: Entity Name Match", "Registration: Current Validity", "Registration: Document Clarity",
        "Shareholder Register: Ownership Consistency", "Shareholder Register: Completeness", "Shareholder Register: Currency",
        "Director Register: Director Consistency", "Director Register: Completeness", "Director Register: Clarity",
        "Financials: Financial Period", "Financials: Entity Name Match", "Financials: Audit Status", "Financials: Completeness",
        "Board Resolution: Signatory Match", "Board Resolution: Date", "Board Resolution: Scope of Authority",
        "Structure Chart: UBO Chain", "Structure Chart: Ownership Match", "Structure Chart: Legibility",
        "Proof of Address: Document Date", "Proof of Address: Entity Name Match", "Proof of Address: Clarity", "Proof of Address: Address Match",
        "Bank Reference: Letterhead", "Bank Reference: Date", "Bank Reference: Entity Name Match",
        "Licence: Entity Name Match", "Licence: Validity", "Licence: Issuing Authority",
        # Person document checks (aligned with PERSON_DOC_CHECKS in back office)
        "Passport: Document Expiry", "Passport: Photo Quality", "Passport: Name Match", "Passport: Nationality Match",
        "Personal PoA: Document Date", "Personal PoA: Name Match", "Personal PoA: Clarity", "Personal PoA: Certification",
        "CV: Name Match", "CV: Employment History",
        "Bank Reference (PEP): Date", "Bank Reference (PEP): Name Match", "Bank Reference (PEP): Bank ID", "Bank Reference (PEP): Account Standing", "Bank Reference (PEP): Signatory"
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
                "Validates authenticity and consistency of uploaded documents against predefined deterministic checks. "
                "Each document type has a fixed set of checks defined in the rule engine — the AI evaluates each check but does NOT decide what checks to run. "
                "Covers entity documents (COI, M&A, registers, financials, etc.) and person documents (passport, PoA, CV, bank reference). "
                "Does NOT do sanctions screening or registry lookups.",
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
    # (must match ENTITY_DOC_CHECKS + PERSON_DOC_CHECKS in backoffice)
    if checks_count == 0:
        ai_checks_seed = [
        # ── Entity documents ──
        ("entity", "poa", "Proof of Registered Address", json.dumps([
            {"id": "DOC-01", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if dated within 3 months. WARN if dated 3-6 months ago. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-02", "label": "Entity Name Match", "rule": "Entity name on document must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-04", "label": "Address Match", "rule": "Address must match registered office address on application. PASS if address matches. WARN if partial match. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-03", "label": "Document Clarity", "rule": "Document must be legible and unredacted. PASS if fully legible. WARN if partially legible. FAIL if illegible or blank.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "cert_inc", "Certificate of Incorporation", json.dumps([
            {"id": "DOC-05", "label": "Entity Name Match", "rule": "Company name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-06", "label": "Registration Number Match", "rule": "Registration number must match the number declared in pre-screening. PASS if present and matches. WARN if partially legible. FAIL if missing or mismatch.", "type": "content"},
            {"id": "DOC-11", "label": "Date of Incorporation Match", "rule": "Date of incorporation must match pre-screening declaration. PASS if date matches. WARN if date differs by less than 6 months. FAIL if mismatch or missing.", "type": "age"},
            {"id": "DOC-12", "label": "Jurisdiction Match", "rule": "Jurisdiction/country of incorporation must match pre-screening. PASS if jurisdiction matches. WARN if abbreviation/variant used. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-07", "label": "Document Clarity", "rule": "Document must be legible, certified copy if applicable. PASS if legible. WARN if partially legible. FAIL if illegible or blank.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "memarts", "Memorandum of Association", json.dumps([
            {"id": "DOC-08", "label": "Entity Name Match", "rule": "Company name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-16", "label": "Authorised Share Capital", "rule": "Authorised share capital must match the amount declared in pre-screening. PASS if capital matches. WARN if minor discrepancy. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-09", "label": "Completeness", "rule": "All pages must be present and legible. PASS if complete and legible. WARN if minor pages missing. FAIL if key pages missing or illegible.", "type": "quality"},
            {"id": "DOC-13", "label": "Business Objects / Activities", "rule": "Declared business activities must fall within the objects clause of the MoA. PASS if activities within objects. WARN if partial overlap. FAIL if activities clearly outside objects. ESCALATE if uncertain.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        # cert_reg retired — no AI checks. Preserved in table for historical records only.
        ("entity", "cert_reg", "Certificate of Registration (Retired)", json.dumps([])),
        ("entity", "reg_sh", "Shareholder Register", json.dumps([
            {"id": "DOC-14", "label": "Shareholder Name Match", "rule": "Shareholder names must match those declared in pre-screening. PASS if all names match (fuzzy > 90%). WARN if minor name variations. FAIL if names cannot be matched or are missing.", "type": "name"},
            {"id": "DOC-15", "label": "Shareholding Percentages Match", "rule": "Shareholding percentages must match those declared in pre-screening. PASS if all percentages match. WARN if minor discrepancies (< 5%). FAIL if major discrepancies or missing.", "type": "content"},
            {"id": "DOC-22", "label": "Total Shares Sum to 100%", "rule": "Total shareholdings must sum to 100%. PASS if totals 100%. WARN if totals 95-100% (rounding). FAIL if < 95% or > 100%.", "type": "content"},
            {"id": "DOC-23", "label": "UBO Identification (\u226525%)", "rule": "Any shareholder holding \u2265 25% must be identified as a declared UBO. PASS if all \u226525% shareholders are declared UBOs. WARN if borderline (24-26%). FAIL if \u226525% shareholder not declared as UBO.", "type": "content"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "reg_dir", "Register of Directors", json.dumps([
            {"id": "DOC-17", "label": "Director Name Match", "rule": "Directors must match those declared in pre-screening. PASS if all directors match (fuzzy > 90%). WARN if minor name variations. FAIL if directors missing or undeclared directors present.", "type": "name"},
            {"id": "DOC-18", "label": "Completeness", "rule": "All current directors must be listed. PASS if all listed. WARN if count uncertain. FAIL if directors clearly missing.", "type": "content"},
            {"id": "DOC-19", "label": "Document Clarity", "rule": "Must be legible. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "fin_stmt", "Financial Statements / Management Accounts", json.dumps([
            {"id": "DOC-20", "label": "Financial Period", "rule": "Must be for most recent financial year (or forecast if < 1 year old). PASS if within last 18 months. WARN if 18-24 months old. FAIL if older than 24 months.", "type": "age"},
            {"id": "DOC-21", "label": "Entity Name Match", "rule": "Company name on statements must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-61", "label": "Revenue / Turnover Consistency", "rule": "Revenue or turnover figures must be broadly consistent with the annual turnover declared in pre-screening. PASS if within 20%. WARN if 20-50% variance. FAIL if > 50% variance or figures missing.", "type": "content"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "board_res", "Board Resolution", json.dumps([
            {"id": "DOC-24", "label": "Signatory Match", "rule": "Authorised signatory must be a declared director. PASS if signatory is a declared director. WARN if name variation. FAIL if signatory not a director.", "type": "name"},
            {"id": "DOC-25", "label": "Resolution Date", "rule": "Must be dated and reasonably current. PASS if dated within 12 months. WARN if 12-24 months old. FAIL if undated or older than 24 months.", "type": "age"},
            {"id": "DOC-26", "label": "Scope of Authority", "rule": "Resolution must explicitly authorise the signatory to open a bank/payment account or engage the relevant service provider. PASS if explicit authorisation present. WARN if implicit only. FAIL if authorisation not found. ESCALATE if legal language is ambiguous.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "structure_chart", "Company Structure Chart", json.dumps([
            {"id": "DOC-27", "label": "UBO Chain", "rule": "Must trace ownership to ultimate beneficial owners. PASS if UBO chain complete. WARN if chain incomplete but UBOs identifiable. FAIL if UBOs not identifiable.", "type": "content"},
            {"id": "DOC-28", "label": "Ownership Match", "rule": "Shareholdings must match shareholder register. PASS if percentages match. WARN if minor discrepancies. FAIL if major discrepancies.", "type": "content"},
            {"id": "DOC-29", "label": "Legibility", "rule": "Diagram must be clear and readable. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "bankref", "Bank Reference Letter (Entity)", json.dumps([
            {"id": "DOC-30", "label": "Bank Letterhead", "rule": "Must be on official bank letterhead. PASS if on letterhead. WARN if letterhead unclear. FAIL if no letterhead.", "type": "quality"},
            {"id": "DOC-31", "label": "Date", "rule": "Must be dated within the last 3 months. PASS if within 3 months. WARN if 3-6 months. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-32", "label": "Entity Name Match", "rule": "Entity name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "licence", "Licence / Regulatory Approval", json.dumps([
            {"id": "DOC-33", "label": "Entity Name Match", "rule": "Entity name must match. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-34", "label": "Licence Validity", "rule": "Licence must be current and not expired. PASS if valid. WARN if expiring within 30 days. FAIL if expired.", "type": "expiry"},
            {"id": "DOC-35", "label": "Licence Scope", "rule": "Licence must cover the business activities declared in the application. PASS if scope covers activities. WARN if partial coverage. FAIL if activities fall outside licence scope. ESCALATE if scope is ambiguous.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        # ── Additional entity documents (used by portal upload flow) ──
        ("entity", "contracts", "Client/Supplier Contracts", json.dumps([
            {"id": "DOC-36", "label": "Name Match", "rule": "Entity name must appear in the contract. PASS if name present and matches. WARN if partial match. FAIL if not present.", "type": "name"},
            {"id": "DOC-37", "label": "Relevance", "rule": "Contract must be relevant to the declared business activity. PASS if relevant. WARN if tangentially related. FAIL if unrelated.", "type": "content"},
            {"id": "DOC-38", "label": "Clarity", "rule": "Document must be legible. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"}
        ])),
        ("entity", "aml_policy", "AML/CFT Policy", json.dumps([
            {"id": "DOC-39", "label": "Completeness", "rule": "Must cover key AML areas (CDD, sanctions screening, reporting). PASS if all key areas covered. WARN if minor gaps. FAIL if major areas missing.", "type": "content"},
            {"id": "DOC-40", "label": "Date", "rule": "Policy must be dated and reviewed within last 12 months. PASS if within 12 months. WARN if 12-24 months. FAIL if older or undated.", "type": "age"},
            {"id": "DOC-41", "label": "Relevance", "rule": "Must be relevant to the entity's business activities. PASS if relevant. WARN if generic. FAIL if irrelevant.", "type": "content"}
        ])),
        ("entity", "source_wealth", "Source of Wealth Documentation", json.dumps([
            {"id": "DOC-42", "label": "Consistency", "rule": "Must be consistent with declared source of wealth in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration.", "type": "content"},
            {"id": "DOC-43", "label": "Clarity", "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible.", "type": "quality"}
        ])),
        ("entity", "source_funds", "Source of Funds Documentation", json.dumps([
            {"id": "DOC-44", "label": "Consistency", "rule": "Must be consistent with declared source of funds in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration.", "type": "content"},
            {"id": "DOC-45", "label": "Clarity", "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible.", "type": "quality"}
        ])),
        ("entity", "bank_statements", "Bank Statements", json.dumps([
            {"id": "DOC-46", "label": "Period", "rule": "Must cover a recent period (within last 6 months). PASS if within 6 months. WARN if 6-12 months. FAIL if older than 12 months.", "type": "age"},
            {"id": "DOC-47", "label": "Name Match", "rule": "Account holder name must match the declared entity or person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-48", "label": "Completeness", "rule": "All pages must be present. PASS if complete. WARN if minor pages missing. FAIL if key pages missing.", "type": "quality"}
        ])),
        # ── Person documents ──
        ("person", "passport", "Passport / Government ID", json.dumps([
            {"id": "DOC-48", "label": "Document Expiry", "rule": "Passport must not be expired. PASS if > 6 months validity remaining. WARN if 1-6 months remaining. FAIL if expired.", "type": "expiry"},
            {"id": "DOC-50", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-62", "label": "Date of Birth Match", "rule": "Date of birth must match the date declared in pre-screening. PASS if date matches. WARN if format variation only. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-63", "label": "Nationality Match", "rule": "Nationality must match declared nationality. PASS if matches. WARN if not clearly visible. FAIL if mismatch.", "type": "content"},
            {"id": "DOC-49", "label": "Photo Quality", "rule": "Photo must be clear and identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("person", "poa", "Proof of Address (Personal)", json.dumps([
            {"id": "DOC-01", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if dated within 3 months. WARN if dated 3-6 months ago. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-52", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-64", "label": "Address Match", "rule": "Address on document must match the person's declared residential address. PASS if address matches. WARN if partial match. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-03", "label": "Document Clarity", "rule": "Document must be legible. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("person", "national_id", "National ID Card", json.dumps([
            {"id": "DOC-53", "label": "Document Expiry", "rule": "Must not be expired. PASS if valid. WARN if expiring within 30 days. FAIL if expired.", "type": "expiry"},
            {"id": "DOC-54", "label": "Photo Quality", "rule": "Photo must be clear and identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable.", "type": "quality"},
            {"id": "DOC-55", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-56", "label": "Nationality Match", "rule": "Nationality must match declared nationality. PASS if matches. WARN if not clearly visible. FAIL if mismatch.", "type": "content"}
        ])),
        ("person", "cv", "CV / LinkedIn Profile", json.dumps([
            {"id": "DOC-57", "label": "Name Match", "rule": "Name on CV/profile must match declared identity. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-58", "label": "Employment History \u2014 Presence", "rule": "CV must include employment history section. PASS if history present. WARN if limited entries. FAIL if no employment history.", "type": "content"},
            {"id": "DOC-65", "label": "Employment History \u2014 Relevance", "rule": "Employment background must be relevant to the declared role and business activity. PASS if background is relevant. WARN if tangentially related. FAIL if no relevant experience. ESCALATE if background appears inconsistent with declared role.", "type": "ai"}
        ])),
        ("person", "sow", "Source of Wealth Declaration", json.dumps([
            {"id": "DOC-59", "label": "Name Match", "rule": "Name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-60", "label": "Source of Wealth Evidence", "rule": "Document must contain credible evidence supporting the declared source of wealth. PASS if credible evidence present. WARN if evidence weak or indirect. FAIL if no evidence. ESCALATE if declared source appears implausible.", "type": "ai"},
            {"id": "DOC-66", "label": "Consistency / Plausibility", "rule": "Declared wealth, function, and source of funds must be internally consistent and plausible. PASS if consistent and plausible. WARN if minor inconsistencies. FAIL if major inconsistencies. ESCALATE for compliance officer review.", "type": "ai"}
        ])),
        ("person", "bankref", "Bank Reference Letter (PEP)", json.dumps([
            {"id": "DOC-67", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if within 3 months. WARN if 3-6 months. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-68", "label": "Name Match", "rule": "Account holder name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-69", "label": "Bank Identification", "rule": "Must be on official bank letterhead with bank name clearly identifiable. PASS if authority clearly identified. WARN if partially identifiable. FAIL if not identifiable.", "type": "content"},
            {"id": "DOC-70", "label": "Account Standing", "rule": "Must confirm account in good standing for at least 12 months. PASS if good standing confirmed. WARN if standing not explicitly confirmed. FAIL if adverse wording or standing not confirmed. ESCALATE if wording is ambiguous.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("person", "pep-declaration", "PEP / Source of Wealth Declaration", json.dumps([
            {"id": "DOC-59", "label": "Name Match", "rule": "Name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-60", "label": "Source of Wealth Evidence", "rule": "Document must contain credible evidence supporting the declared source of wealth. PASS if credible evidence present. WARN if evidence weak or indirect. FAIL if no evidence. ESCALATE if declared source appears implausible.", "type": "ai"},
            {"id": "DOC-66", "label": "Consistency / Plausibility", "rule": "Declared wealth, function, and source of funds must be internally consistent and plausible. PASS if consistent and plausible. WARN if minor inconsistencies. FAIL if major inconsistencies. ESCALATE for compliance officer review.", "type": "ai"}
        ])),
        ]

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

    # Sprint 3: Seed default GDPR data retention policies
    # Based on Mauritius Data Protection Act 2017 + GDPR Article 5(1)(e)
    retention_policies = [
        ("client_pii", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "Client personal data: names, addresses, DOB, nationality. 7 years post-relationship.", 0, 1),
        ("kyc_documents", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "KYC/CDD documents: passports, proof of address, corporate registry. 7 years post-relationship.", 0, 1),
        ("screening_results", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "Sanctions, PEP, adverse media screening results. 7 years retention.", 0, 1),
        ("compliance_memos", 2555, "Regulatory obligation (AML/CFT Act 2020 s.17)", "Compliance memos and risk assessments. 7 years retention.", 0, 1),
        ("audit_logs", 3650, "Legitimate interest + regulatory", "Audit trail records. 10 years retention for full accountability.", 0, 0),
        ("application_data", 2555, "Regulatory obligation", "Onboarding application forms and submitted data. 7 years post-decision.", 0, 1),
        ("sar_reports", 3650, "Regulatory obligation (FIU reporting)", "Suspicious Activity Reports. 10 years — never auto-purge.", 0, 0),
        ("session_tokens", 1, "Legitimate interest", "Expired authentication tokens and session data. 24-hour retention.", 1, 0),
        ("monitoring_alerts", 2555, "Regulatory obligation", "Ongoing monitoring alerts and risk drift records. 7 years.", 0, 1),
    ]

    for policy in retention_policies:
        try:
            db.execute(
                "INSERT OR IGNORE INTO data_retention_policies (data_category, retention_days, legal_basis, description, auto_purge, requires_review) VALUES (?,?,?,?,?,?)",
                policy
            )
        except Exception as e:
            logger.debug(f"Data retention policy '{policy[0]}' already seeded or insert failed: {e}")

    db.commit()
    logger.info("Database seeded with initial data")


def normalize_legacy_doc_types(db: DBConnection):
    """
    Idempotent migration: normalize legacy portal-style doc_type values in the
    documents table to canonical backend values.

    Runs on every startup. Only updates rows whose doc_type matches a known
    legacy key; already-canonical rows are untouched.
    """
    _DOC_TYPE_NORMALIZE = {
        "doc-coi": "cert_inc", "doc-memarts": "memarts", "doc-shareholders": "reg_sh",
        "doc-directors-reg": "reg_dir", "doc-financials": "fin_stmt", "doc-proof-address": "poa",
        "doc-board-res": "board_res", "doc-structure-chart": "structure_chart",
        "doc-bank-ref": "bankref", "doc-license-cert": "licence",
        "doc-contracts": "contracts", "doc-source-wealth-proof": "source_wealth",
        "doc-source-funds-proof": "source_funds", "doc-bank-statements": "bank_statements",
        "doc-aml-policy": "aml_policy",
    }
    total_updated = 0
    for old_type, new_type in _DOC_TYPE_NORMALIZE.items():
        try:
            db.execute(
                "UPDATE documents SET doc_type=? WHERE doc_type=?",
                (new_type, old_type)
            )
            # rowcount is not always reliable across DB adapters, so we count separately
            count_row = db.execute(
                "SELECT COUNT(*) as cnt FROM documents WHERE doc_type=?",
                (old_type,)
            ).fetchone()
            # If the count is 0 after update, the update worked (or there were none)
        except Exception as e:
            logger.warning(f"normalize_legacy_doc_types: failed to update {old_type} -> {new_type}: {e}")
    # Log summary
    remaining = 0
    for old_type in _DOC_TYPE_NORMALIZE:
        try:
            row = db.execute("SELECT COUNT(*) as cnt FROM documents WHERE doc_type=?", (old_type,)).fetchone()
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
    ai_checks_seed = [
        # ── Entity documents ──
        ("entity", "poa", "Proof of Registered Address", json.dumps([
            {"id": "DOC-01", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if dated within 3 months. WARN if dated 3-6 months ago. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-02", "label": "Entity Name Match", "rule": "Entity name on document must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-04", "label": "Address Match", "rule": "Address must match registered office address on application. PASS if address matches. WARN if partial match. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-03", "label": "Document Clarity", "rule": "Document must be legible and unredacted. PASS if fully legible. WARN if partially legible. FAIL if illegible or blank.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "cert_inc", "Certificate of Incorporation", json.dumps([
            {"id": "DOC-05", "label": "Entity Name Match", "rule": "Company name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-06", "label": "Registration Number Match", "rule": "Registration number must match the number declared in pre-screening. PASS if present and matches. WARN if partially legible. FAIL if missing or mismatch.", "type": "content"},
            {"id": "DOC-11", "label": "Date of Incorporation Match", "rule": "Date of incorporation must match pre-screening declaration. PASS if date matches. WARN if date differs by less than 6 months. FAIL if mismatch or missing.", "type": "age"},
            {"id": "DOC-12", "label": "Jurisdiction Match", "rule": "Jurisdiction/country of incorporation must match pre-screening. PASS if jurisdiction matches. WARN if abbreviation/variant used. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-07", "label": "Document Clarity", "rule": "Document must be legible, certified copy if applicable. PASS if legible. WARN if partially legible. FAIL if illegible or blank.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "memarts", "Memorandum of Association", json.dumps([
            {"id": "DOC-08", "label": "Entity Name Match", "rule": "Company name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-16", "label": "Authorised Share Capital", "rule": "Authorised share capital must match the amount declared in pre-screening. PASS if capital matches. WARN if minor discrepancy. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-09", "label": "Completeness", "rule": "All pages must be present and legible. PASS if complete and legible. WARN if minor pages missing. FAIL if key pages missing or illegible.", "type": "quality"},
            {"id": "DOC-13", "label": "Business Objects / Activities", "rule": "Declared business activities must fall within the objects clause of the MoA. PASS if activities within objects. WARN if partial overlap. FAIL if activities clearly outside objects. ESCALATE if uncertain.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        # cert_reg retired — no AI checks. Preserved in table for historical records only.
        ("entity", "cert_reg", "Certificate of Registration (Retired)", json.dumps([])),
        ("entity", "reg_sh", "Shareholder Register", json.dumps([
            {"id": "DOC-14", "label": "Shareholder Name Match", "rule": "Shareholder names must match those declared in pre-screening. PASS if all names match (fuzzy > 90%). WARN if minor name variations. FAIL if names cannot be matched or are missing.", "type": "name"},
            {"id": "DOC-15", "label": "Shareholding Percentages Match", "rule": "Shareholding percentages must match those declared in pre-screening. PASS if all percentages match. WARN if minor discrepancies (< 5%). FAIL if major discrepancies or missing.", "type": "content"},
            {"id": "DOC-22", "label": "Total Shares Sum to 100%", "rule": "Total shareholdings must sum to 100%. PASS if totals 100%. WARN if totals 95-100% (rounding). FAIL if < 95% or > 100%.", "type": "content"},
            {"id": "DOC-23", "label": "UBO Identification (\u226525%)", "rule": "Any shareholder holding \u2265 25% must be identified as a declared UBO. PASS if all \u226525% shareholders are declared UBOs. WARN if borderline (24-26%). FAIL if \u226525% shareholder not declared as UBO.", "type": "content"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "reg_dir", "Register of Directors", json.dumps([
            {"id": "DOC-17", "label": "Director Name Match", "rule": "Directors must match those declared in pre-screening. PASS if all directors match (fuzzy > 90%). WARN if minor name variations. FAIL if directors missing or undeclared directors present.", "type": "name"},
            {"id": "DOC-18", "label": "Completeness", "rule": "All current directors must be listed. PASS if all listed. WARN if count uncertain. FAIL if directors clearly missing.", "type": "content"},
            {"id": "DOC-19", "label": "Document Clarity", "rule": "Must be legible. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "fin_stmt", "Financial Statements / Management Accounts", json.dumps([
            {"id": "DOC-20", "label": "Financial Period", "rule": "Must be for most recent financial year (or forecast if < 1 year old). PASS if within last 18 months. WARN if 18-24 months old. FAIL if older than 24 months.", "type": "age"},
            {"id": "DOC-21", "label": "Entity Name Match", "rule": "Company name on statements must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-61", "label": "Revenue / Turnover Consistency", "rule": "Revenue or turnover figures must be broadly consistent with the annual turnover declared in pre-screening. PASS if within 20%. WARN if 20-50% variance. FAIL if > 50% variance or figures missing.", "type": "content"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "board_res", "Board Resolution", json.dumps([
            {"id": "DOC-24", "label": "Signatory Match", "rule": "Authorised signatory must be a declared director. PASS if signatory is a declared director. WARN if name variation. FAIL if signatory not a director.", "type": "name"},
            {"id": "DOC-25", "label": "Resolution Date", "rule": "Must be dated and reasonably current. PASS if dated within 12 months. WARN if 12-24 months old. FAIL if undated or older than 24 months.", "type": "age"},
            {"id": "DOC-26", "label": "Scope of Authority", "rule": "Resolution must explicitly authorise the signatory to open a bank/payment account or engage the relevant service provider. PASS if explicit authorisation present. WARN if implicit only. FAIL if authorisation not found. ESCALATE if legal language is ambiguous.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "structure_chart", "Company Structure Chart", json.dumps([
            {"id": "DOC-27", "label": "UBO Chain", "rule": "Must trace ownership to ultimate beneficial owners. PASS if UBO chain complete. WARN if chain incomplete but UBOs identifiable. FAIL if UBOs not identifiable.", "type": "content"},
            {"id": "DOC-28", "label": "Ownership Match", "rule": "Shareholdings must match shareholder register. PASS if percentages match. WARN if minor discrepancies. FAIL if major discrepancies.", "type": "content"},
            {"id": "DOC-29", "label": "Legibility", "rule": "Diagram must be clear and readable. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "bankref", "Bank Reference Letter (Entity)", json.dumps([
            {"id": "DOC-30", "label": "Bank Letterhead", "rule": "Must be on official bank letterhead. PASS if on letterhead. WARN if letterhead unclear. FAIL if no letterhead.", "type": "quality"},
            {"id": "DOC-31", "label": "Date", "rule": "Must be dated within the last 3 months. PASS if within 3 months. WARN if 3-6 months. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-32", "label": "Entity Name Match", "rule": "Entity name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "licence", "Licence / Regulatory Approval", json.dumps([
            {"id": "DOC-33", "label": "Entity Name Match", "rule": "Entity name must match. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-34", "label": "Licence Validity", "rule": "Licence must be current and not expired. PASS if valid. WARN if expiring within 30 days. FAIL if expired.", "type": "expiry"},
            {"id": "DOC-35", "label": "Licence Scope", "rule": "Licence must cover the business activities declared in the application. PASS if scope covers activities. WARN if partial coverage. FAIL if activities fall outside licence scope. ESCALATE if scope is ambiguous.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("entity", "contracts", "Client/Supplier Contracts", json.dumps([
            {"id": "DOC-36", "label": "Name Match", "rule": "Entity name must appear in the contract. PASS if name present and matches. WARN if partial match. FAIL if not present.", "type": "name"},
            {"id": "DOC-37", "label": "Relevance", "rule": "Contract must be relevant to the declared business activity. PASS if relevant. WARN if tangentially related. FAIL if unrelated.", "type": "content"},
            {"id": "DOC-38", "label": "Clarity", "rule": "Document must be legible. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"}
        ])),
        ("entity", "aml_policy", "AML/CFT Policy", json.dumps([
            {"id": "DOC-39", "label": "Completeness", "rule": "Must cover key AML areas (CDD, sanctions screening, reporting). PASS if all key areas covered. WARN if minor gaps. FAIL if major areas missing.", "type": "content"},
            {"id": "DOC-40", "label": "Date", "rule": "Policy must be dated and reviewed within last 12 months. PASS if within 12 months. WARN if 12-24 months. FAIL if older or undated.", "type": "age"},
            {"id": "DOC-41", "label": "Relevance", "rule": "Must be relevant to the entity's business activities. PASS if relevant. WARN if generic. FAIL if irrelevant.", "type": "content"}
        ])),
        ("entity", "source_wealth", "Source of Wealth Documentation", json.dumps([
            {"id": "DOC-42", "label": "Consistency", "rule": "Must be consistent with declared source of wealth in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration.", "type": "content"},
            {"id": "DOC-43", "label": "Clarity", "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible.", "type": "quality"}
        ])),
        ("entity", "source_funds", "Source of Funds Documentation", json.dumps([
            {"id": "DOC-44", "label": "Consistency", "rule": "Must be consistent with declared source of funds in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration.", "type": "content"},
            {"id": "DOC-45", "label": "Clarity", "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible.", "type": "quality"}
        ])),
        ("entity", "bank_statements", "Bank Statements", json.dumps([
            {"id": "DOC-46", "label": "Period", "rule": "Must cover a recent period (within last 6 months). PASS if within 6 months. WARN if 6-12 months. FAIL if older than 12 months.", "type": "age"},
            {"id": "DOC-47", "label": "Name Match", "rule": "Account holder name must match the declared entity or person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-48", "label": "Completeness", "rule": "All pages must be present. PASS if complete. WARN if minor pages missing. FAIL if key pages missing.", "type": "quality"}
        ])),
        # ── Person documents ──
        ("person", "passport", "Passport / Government ID", json.dumps([
            {"id": "DOC-48", "label": "Document Expiry", "rule": "Passport must not be expired. PASS if > 6 months validity remaining. WARN if 1-6 months remaining. FAIL if expired.", "type": "expiry"},
            {"id": "DOC-50", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-62", "label": "Date of Birth Match", "rule": "Date of birth must match the date declared in pre-screening. PASS if date matches. WARN if format variation only. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-63", "label": "Nationality Match", "rule": "Nationality must match declared nationality. PASS if matches. WARN if not clearly visible. FAIL if mismatch.", "type": "content"},
            {"id": "DOC-49", "label": "Photo Quality", "rule": "Photo must be clear and identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("person", "poa", "Proof of Address (Personal)", json.dumps([
            {"id": "DOC-01", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if dated within 3 months. WARN if dated 3-6 months ago. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-52", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-64", "label": "Address Match", "rule": "Address on document must match the person's declared residential address. PASS if address matches. WARN if partial match. FAIL if mismatch or missing.", "type": "content"},
            {"id": "DOC-03", "label": "Document Clarity", "rule": "Document must be legible. PASS if legible. WARN if partially legible. FAIL if illegible.", "type": "quality"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("person", "national_id", "National ID Card", json.dumps([
            {"id": "DOC-53", "label": "Document Expiry", "rule": "Must not be expired. PASS if valid. WARN if expiring within 30 days. FAIL if expired.", "type": "expiry"},
            {"id": "DOC-54", "label": "Photo Quality", "rule": "Photo must be clear and identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable.", "type": "quality"},
            {"id": "DOC-55", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-56", "label": "Nationality Match", "rule": "Nationality must match declared nationality. PASS if matches. WARN if not clearly visible. FAIL if mismatch.", "type": "content"}
        ])),
        ("person", "cv", "CV / LinkedIn Profile", json.dumps([
            {"id": "DOC-57", "label": "Name Match", "rule": "Name on CV/profile must match declared identity. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-58", "label": "Employment History \u2014 Presence", "rule": "CV must include employment history section. PASS if history present. WARN if limited entries. FAIL if no employment history.", "type": "content"},
            {"id": "DOC-65", "label": "Employment History \u2014 Relevance", "rule": "Employment background must be relevant to the declared role and business activity. PASS if background is relevant. WARN if tangentially related. FAIL if no relevant experience. ESCALATE if background appears inconsistent with declared role.", "type": "ai"}
        ])),
        ("person", "sow", "Source of Wealth Declaration", json.dumps([
            {"id": "DOC-59", "label": "Name Match", "rule": "Name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-60", "label": "Source of Wealth Evidence", "rule": "Document must contain credible evidence supporting the declared source of wealth. PASS if credible evidence present. WARN if evidence weak or indirect. FAIL if no evidence. ESCALATE if declared source appears implausible.", "type": "ai"},
            {"id": "DOC-66", "label": "Consistency / Plausibility", "rule": "Declared wealth, function, and source of funds must be internally consistent and plausible. PASS if consistent and plausible. WARN if minor inconsistencies. FAIL if major inconsistencies. ESCALATE for compliance officer review.", "type": "ai"}
        ])),
        ("person", "bankref", "Bank Reference Letter (PEP)", json.dumps([
            {"id": "DOC-67", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if within 3 months. WARN if 3-6 months. FAIL if older than 6 months or undated.", "type": "age"},
            {"id": "DOC-68", "label": "Name Match", "rule": "Account holder name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-69", "label": "Bank Identification", "rule": "Must be on official bank letterhead with bank name clearly identifiable. PASS if authority clearly identified. WARN if partially identifiable. FAIL if not identifiable.", "type": "content"},
            {"id": "DOC-70", "label": "Account Standing", "rule": "Must confirm account in good standing for at least 12 months. PASS if good standing confirmed. WARN if standing not explicitly confirmed. FAIL if adverse wording or standing not confirmed. ESCALATE if wording is ambiguous.", "type": "ai"},
            {"id": "CERT-01", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification.", "type": "quality"}
        ])),
        ("person", "pep-declaration", "PEP / Source of Wealth Declaration", json.dumps([
            {"id": "DOC-59", "label": "Name Match", "rule": "Name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing.", "type": "name"},
            {"id": "DOC-60", "label": "Source of Wealth Evidence", "rule": "Document must contain credible evidence supporting the declared source of wealth. PASS if credible evidence present. WARN if evidence weak or indirect. FAIL if no evidence. ESCALATE if declared source appears implausible.", "type": "ai"},
            {"id": "DOC-66", "label": "Consistency / Plausibility", "rule": "Declared wealth, function, and source of funds must be internally consistent and plausible. PASS if consistent and plausible. WARN if minor inconsistencies. FAIL if major inconsistencies. ESCALATE for compliance officer review.", "type": "ai"}
        ])),
    ]

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

            # Insert rows into PostgreSQL
            for row in rows:
                values = tuple(row)
                try:
                    pg_cursor.execute(insert_sql, values)
                except Exception as e:
                    logger.warning(f"  Error inserting row into {table}: {e}")
                    pg_conn.rollback()

            pg_conn.commit()
            logger.info(f"  Migrated {len(rows)} rows")

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
