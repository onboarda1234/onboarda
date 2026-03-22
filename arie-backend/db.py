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
# Configuration
# ============================================================================

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DB_PATH", "./arie_finance.db")
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
            2, 10,
            DATABASE_URL,
            sslmode='require'
        )
        logger.info("PostgreSQL connection pool initialized")


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

    def _translate_placeholders(self, sql: str) -> str:
        """Translate between ? and %s placeholders."""
        if not self.is_postgres:
            return sql
        # Convert ? to %s for PostgreSQL
        return sql.replace('?', '%s')

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
        """Execute SQL query."""
        cursor = self._cursor_or_create()
        sql = self._translate_placeholders(sql)
        cursor.execute(sql, params)
        return self

    def executescript(self, sql: str) -> None:
        """Execute multiple SQL statements (SQLite only)."""
        if self.is_postgres:
            # For PostgreSQL, split and execute individually
            cursor = self._cursor_or_create()
            for statement in sql.split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
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
        return row  # sqlite3.Row supports both row["key"] and row[0]

    def fetchall(self):
        """Fetch all rows. Returns list of sqlite3.Row (SQLite) or list of dict (PostgreSQL)."""
        cursor = self._cursor_or_create()
        rows = cursor.fetchall()
        if self.is_postgres:
            return [dict(row) for row in rows]
        return rows  # list of sqlite3.Row

    def commit(self) -> None:
        """Commit transaction."""
        self.conn.commit()

    def close(self) -> None:
        """Close connection and return to pool if PostgreSQL."""
        if self._cursor:
            self._cursor.close()
        if self.is_postgres:
            # Return connection to pool
            global _pg_pool
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
    env = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "development")).lower()

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
            'draft','prescreening_submitted','pricing_review','pricing_accepted',
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Directors
    CREATE TABLE IF NOT EXISTS directors (
        id TEXT PRIMARY KEY DEFAULT encode(gen_random_bytes(8), 'hex'),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
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
        full_name TEXT NOT NULL,
        nationality TEXT,
        ownership_pct REAL,
        is_pep BOOLEAN DEFAULT false,
        pep_declaration JSONB DEFAULT '{}',
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
        file_size INTEGER,
        mime_type TEXT,
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','flagged','failed')),
        verification_results JSONB DEFAULT '{}',
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        verified_at TIMESTAMP
    );

    -- Risk Model Configuration
    CREATE TABLE IF NOT EXISTS risk_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        dimensions JSONB NOT NULL DEFAULT '{}',
        thresholds JSONB NOT NULL DEFAULT '{}',
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
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_application_id ON monitoring_alerts(application_id);
    CREATE INDEX IF NOT EXISTS idx_periodic_reviews_application_id ON periodic_reviews(application_id);
    CREATE INDEX IF NOT EXISTS idx_sar_reports_application_id ON sar_reports(application_id);
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
            'draft','prescreening_submitted','pricing_review','pricing_accepted',
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
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Directors
    CREATE TABLE IF NOT EXISTS directors (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
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
        full_name TEXT NOT NULL,
        nationality TEXT,
        ownership_pct REAL,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}',
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
        file_size INTEGER,
        mime_type TEXT,
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','flagged','failed')),
        verification_results TEXT DEFAULT '{}',
        uploaded_at TEXT DEFAULT (datetime('now')),
        verified_at TEXT
    );

    -- Risk Model Configuration
    CREATE TABLE IF NOT EXISTS risk_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        dimensions TEXT NOT NULL DEFAULT '{}',
        thresholds TEXT NOT NULL DEFAULT '{}',
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
    """


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
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise
    finally:
        db.close()


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


# ============================================================================
# Seed Data
# ============================================================================

def seed_initial_data(db: DBConnection):
    """Seed database with initial admin users, risk config, and AI agents."""
    import bcrypt

    # Check if users already exist
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count > 0:
        logger.info("Database already seeded, skipping initialization")
        return

    # Generate initial admin password
    init_password = os.environ.get("ADMIN_INITIAL_PASSWORD", "")
    if not init_password:
        init_password = secrets.token_urlsafe(16)
        print(f"\n  ⚠️  INITIAL ADMIN PASSWORD (save this now): {init_password}")
        print(f"  ⚠️  Change it immediately after first login.\n")

    pw_hash = bcrypt.hashpw(init_password.encode(), bcrypt.gensalt()).decode()

    # Seed admin users
    db.execute(
        "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("admin001", "asudally@ariefinance.mu", pw_hash, "Aisha Sudally", "admin", "active")
    )
    db.execute(
        "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("sco001", "raj.patel@ariefinance.mu", pw_hash, "Raj Patel", "sco", "active")
    )
    db.execute(
        "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("co001", "m.dubois@ariefinance.mu", pw_hash, "Marie Dubois", "co", "active")
    )
    db.execute(
        "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("analyst001", "l.wei@ariefinance.mu", pw_hash, "Li Wei", "analyst", "active")
    )
    db.commit()

    # Seed risk config
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
                {"name": "Country of Incorporation", "weight": 40},
                {"name": "Countries of Operation", "weight": 30},
                {"name": "Target Markets", "weight": 30}
            ]
        },
        {
            "id": "D3", "name": "Product / Service Risk", "weight": 20,
            "subcriteria": [
                {"name": "Service Type", "weight": 40},
                {"name": "Monthly Volume", "weight": 30},
                {"name": "Transaction Complexity", "weight": 30}
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
    db.execute(
        "INSERT INTO risk_config (id, dimensions, thresholds) VALUES (?, ?, ?)",
        (1, default_dims, default_thresholds)
    )

    # Seed AI agents (10-agent architecture)
    agent1_checks = json.dumps([
        "Detect passport type", "Extract MRZ (machine-readable zone)", "Verify MRZ checksum",
        "Match name with application data", "Check date of birth consistency",
        "Extract expiry date", "Check if expired", "Check if near expiry",
        "Validate issue date vs expiry rules", "Detect tampering",
        "Detect image manipulation", "Verify watermark patterns", "Detect cropped or incomplete documents",
        "Passport name vs application name", "Passport address vs proof of address",
        "Director name vs registry data", "Missing pages detection",
        "Missing UBO documents", "Incomplete shareholder register",
        "Blur detection", "Cropping detection", "Tampering detection",
        "Certificate of incorporation validity", "Shareholder register completeness",
        "Director list consistency", "Nationality consistency",
        "Proof of address issue date", "Proof of address address match",
        "Proof of address document type validity", "Document type classification",
        "Image quality assessment", "Document orientation check",
        "Multi-page document completeness", "Signature presence check",
        "Photo quality assessment", "Document language detection"
    ])
    agent2_checks = json.dumps([
        "Company registry verification", "Director name verification",
        "Shareholder verification", "Jurisdiction validation",
        "Company status check (active/dissolved)", "Registration number verification",
        "Incorporation date verification", "Registered address verification",
        "Company type verification", "Filed documents check",
        "Cross-reference directors with passport data", "Cross-reference shareholders with UBO declarations"
    ])
    agent3_checks = json.dumps([
        "Sanctions list screening", "PEP database screening",
        "Watchlist screening", "Adverse media screening",
        "False positive assessment", "Match confidence scoring",
        "Material adverse media identification", "Political exposure classification",
        "Screening result interpretation", "Risk relevance assessment"
    ])
    agent4_checks = json.dumps([
        "Map ownership layers", "Identify ultimate beneficial owners",
        "Detect nominee structures", "Flag complex ownership chains",
        "Calculate ownership percentages", "Identify high-risk jurisdiction links",
        "Detect shell company indicators", "Verify UBO identity documents",
        "Cross-reference UBOs with sanctions/PEP results", "Structure complexity scoring"
    ])
    agent5_checks = json.dumps([
        "Compile all agent results", "Summarize key findings",
        "Identify risk indicators", "Recommend risk rating",
        "Generate onboarding memo", "Produce review checklist",
        "Flag unresolved contradictions", "Calculate aggregate confidence",
        "Determine approval/escalation recommendation", "Generate compliance narrative"
    ])

    agents_seed = [
        (
            1, "Identity & Document Integrity Agent", "🔍", "document_verification",
            "Validates authenticity and internal consistency of identity documents uploaded during onboarding. "
            "Performs ~36 automated checks including: passport MRZ extraction and checksum verification, expiry date validation, "
            "tampering and image manipulation detection, cross-document consistency (passport vs application, address vs proof of address, "
            "director names vs registry data), missing documentation detection, blur/cropping detection, certificate of incorporation validity, "
            "and shareholder register completeness. Focuses ONLY on document authenticity, expiry, identity extraction, and cross-document consistency. "
            "Does NOT perform sanctions screening or registry lookups — passes extracted data to downstream agents. "
            "Output: Document Verification Result with verification status, expiry status, tampering risk, name match score, and document completeness.",
            1, agent1_checks
        ),
        (
            2, "Corporate Structure & UBO Mapping Agent", "🏗️", "corporate_ubo",
            "Maps ownership chains, identifies ultimate beneficial owners (natural persons), detects nominee structures, "
            "flags complex ownership chains, calculates ownership percentages through layered structures, "
            "cross-references directors/shareholders against external registries (OpenCorporates, Companies House, ADGM, DIFC), "
            "identifies high-risk jurisdiction links, detects shell company indicators. "
            "Output: ownership map, UBO list, registry cross-verification results, structure complexity score, and nominee/shell risk flags.",
            1, agent2_checks
        ),
        (
            3, "Business Model Plausibility Agent", "📊", "business_plausibility",
            "Evaluates business story, sector alignment, transaction benchmarking, and source of funds consistency. "
            "Analyses declared business model against industry norms, assesses revenue model plausibility, "
            "identifies geographic risk factors, flags unusual transaction patterns, "
            "and checks regulatory licence requirements. "
            "Output: plausibility score, sector risk assessment, transaction pattern concerns, and red flags.",
            1, agent3_checks
        ),
        (
            4, "FinCrime Screening Interpretation Agent", "💼", "screening",
            "Screens individuals and entities against sanctions lists, PEP databases, watchlists, and adverse media sources "
            "via Sumsub AML. Interprets raw screening hits using Claude AI to distinguish false positives from genuine matches, "
            "assesses match confidence, consolidates duplicate hits, ranks severity, highlights material adverse media, "
            "and classifies political exposure levels. "
            "Output: Screening Result with sanctions match status, PEP status (with confidence level), adverse media findings, "
            "false positive assessment, and overall screening risk assessment.",
            1, agent4_checks
        ),
        (
            5, "Compliance Memo & Risk Recommendation Agent", "📝", "compliance_memo",
            "Final synthesis agent. After Agents 1-4 complete their checks, this agent: "
            "computes 5-dimension composite risk scoring (Entity 30%, Geographic 25%, Product/Service 20%, Sector 15%, Channel 10%), "
            "compiles all results into a structured onboarding memo, summarizes key findings across all verification layers, "
            "identifies and ranks risk indicators, recommends a risk rating (LOW/MEDIUM/HIGH/VERY_HIGH), "
            "flags any unresolved contradictions between agents, calculates aggregate confidence score, "
            "and produces a review checklist for compliance officers. "
            "Output: structured onboarding report, 5-dimension risk score, risk recommendation, review checklist, and approval/escalation recommendation.",
            1, agent5_checks
        ),
        (
            6, "Periodic Review Preparation Agent", "📅", "periodic_review",
            "Prepares client files for periodic reviews, identifies expired documents, "
            "requests updated information, and summarises changes since onboarding.",
            1, "[]"
        ),
        (
            7, "Adverse Media & PEP Monitoring Agent", "📡", "media_monitoring",
            "Continuous media monitoring, PEP status changes, new sanctions exposure, "
            "enforcement actions. Output: alert summaries, severity classification.",
            1, "[]"
        ),
        (
            8, "Behaviour & Risk Drift Agent", "📈", "risk_drift",
            "Compares current activity vs onboarding profile, detects new jurisdictions, "
            "identifies sector changes, flags unusual growth patterns. Output: risk drift score, escalation trigger.",
            1, "[]"
        ),
        (
            9, "Regulatory Impact Agent", "⚖️", "regulatory_impact",
            "Analyses new circulars/rules, identifies impacted client segments, "
            "triggers compliance actions. Output: regulatory impact summary, remediation tasks.",
            1, "[]"
        ),
        (
            10, "Ongoing Compliance Review Agent", "📋", "compliance_review",
            "Consolidates monitoring alerts, summarises risk changes, recommends actions "
            "(maintain/EDD/exit). Output: periodic review memo, updated risk classification, escalation recommendation.",
            1, "[]"
        )
    ]

    # Supervisor agent type mapping: agent_number → supervisor schema AgentType
    supervisor_type_map = {
        1: "identity_document_integrity",
        2: "corporate_structure_ubo",
        3: "business_model_plausibility",
        4: "fincrime_screening",
        5: "compliance_memo_risk",
        6: "periodic_review_preparation",
        7: "adverse_media_pep_monitoring",
        8: "behaviour_risk_drift",
        9: None,  # Regulatory Impact Agent has no supervisor equivalent yet
        10: "ongoing_compliance_review",
    }

    for agent_data in agents_seed:
        agent_num = agent_data[0]
        sv_type = supervisor_type_map.get(agent_num)
        db.execute(
            "INSERT INTO ai_agents (agent_number, name, icon, stage, description, enabled, checks, supervisor_agent_type) VALUES (?,?,?,?,?,?,?,?)",
            agent_data + (sv_type,)
        )

    db.commit()

    # Seed monitoring agents status
    now = datetime.now().isoformat()
    next_day = (datetime.now() + timedelta(days=1)).isoformat()
    next_week = (datetime.now() + timedelta(days=7)).isoformat()
    next_month = (datetime.now() + timedelta(days=30)).isoformat()

    agents_status = [
        ("Sanctions/PEP Agent", "sanctions_pep", now, next_day, "Daily", 45, 2, "active"),
        ("Adverse Media Agent", "adverse_media", now, (datetime.now() + timedelta(hours=6)).isoformat(), "Every 6 hours", 45, 1, "active"),
        ("Registry Monitoring Agent", "registry", (datetime.now() - timedelta(days=7)).isoformat(), next_week, "Weekly", 45, 0, "active"),
        ("Risk Drift Agent", "risk_drift", (datetime.now() - timedelta(days=30)).isoformat(), next_month, "Monthly", 45, 3, "active"),
        ("Regulatory Impact Agent", "regulatory", (datetime.now() - timedelta(days=14)).isoformat(), next_month, "On circular publication", 45, 1, "active"),
    ]

    for agent_data in agents_status:
        db.execute(
            "INSERT INTO monitoring_agent_status (agent_name, agent_type, last_run, next_run, run_frequency, clients_monitored, alerts_generated, status) VALUES (?,?,?,?,?,?,?,?)",
            agent_data
        )

    db.commit()

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
        except Exception:
            try:
                db.execute(
                    "INSERT INTO data_retention_policies (data_category, retention_days, legal_basis, description, auto_purge, requires_review) VALUES (?,?,?,?,?,?) ON CONFLICT (data_category) DO NOTHING",
                    policy
                )
            except Exception:
                pass  # Already seeded

    db.commit()
    logger.info("Database seeded with initial data")


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
