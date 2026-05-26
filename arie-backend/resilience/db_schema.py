#!/usr/bin/env python3
"""
Database schema for the resilience layer.
Creates tables for circuit breaker state, external API retry queue, and metrics.
"""

import logging
import sqlite3
import aiosqlite
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# SQL Schema definitions
CIRCUIT_BREAKER_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'CLOSED',
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_failure_at TEXT,
    opened_at TEXT,
    last_state_change_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

EXTERNAL_RETRY_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS external_retry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    application_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    next_retry_at TEXT NOT NULL,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

EXTERNAL_API_ATTEMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS external_api_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    application_id TEXT,
    status_code INTEGER,
    latency_ms INTEGER,
    retry_count INTEGER DEFAULT 0,
    circuit_state TEXT,
    outcome TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);
"""

# Index definitions for performance
CIRCUIT_BREAKER_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_circuit_breaker_provider ON circuit_breaker_state(provider);",
    "CREATE INDEX IF NOT EXISTS idx_circuit_breaker_state ON circuit_breaker_state(state);",
]

EXTERNAL_RETRY_QUEUE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_external_retry_queue_status ON external_retry_queue(status);",
    "CREATE INDEX IF NOT EXISTS idx_external_retry_queue_provider ON external_retry_queue(provider);",
    "CREATE INDEX IF NOT EXISTS idx_external_retry_queue_application_id ON external_retry_queue(application_id);",
    "CREATE INDEX IF NOT EXISTS idx_external_retry_queue_next_retry ON external_retry_queue(next_retry_at);",
]

EXTERNAL_API_ATTEMPTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_external_api_attempts_provider ON external_api_attempts(provider);",
    "CREATE INDEX IF NOT EXISTS idx_external_api_attempts_application_id ON external_api_attempts(application_id);",
    "CREATE INDEX IF NOT EXISTS idx_external_api_attempts_outcome ON external_api_attempts(outcome);",
    "CREATE INDEX IF NOT EXISTS idx_external_api_attempts_created_at ON external_api_attempts(created_at);",
]


async def init_resilience_tables(db_path: str) -> None:
    """
    Initialize all resilience layer tables and indexes.

    Args:
        db_path: Path to the SQLite database file
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            logger.info(f"Initializing resilience tables at {db_path}")

            # Create tables
            await db.execute(CIRCUIT_BREAKER_STATE_TABLE)
            await db.execute(EXTERNAL_RETRY_QUEUE_TABLE)
            await db.execute(EXTERNAL_API_ATTEMPTS_TABLE)

            # Create indexes
            for index_sql in CIRCUIT_BREAKER_INDEXES:
                await db.execute(index_sql)

            for index_sql in EXTERNAL_RETRY_QUEUE_INDEXES:
                await db.execute(index_sql)

            for index_sql in EXTERNAL_API_ATTEMPTS_INDEXES:
                await db.execute(index_sql)

            await db.commit()
            logger.info("Resilience tables initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize resilience tables: {e}", exc_info=True)
        raise


def init_resilience_tables_sync(db_path: str) -> None:
    """
    Synchronous version of init_resilience_tables for startup initialization.

    Args:
        db_path: Path to the SQLite database file
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        logger.info(f"Initializing resilience tables at {db_path}")

        # Create tables
        cursor.execute(CIRCUIT_BREAKER_STATE_TABLE)
        cursor.execute(EXTERNAL_RETRY_QUEUE_TABLE)
        cursor.execute(EXTERNAL_API_ATTEMPTS_TABLE)

        # Create indexes
        for index_sql in CIRCUIT_BREAKER_INDEXES:
            cursor.execute(index_sql)

        for index_sql in EXTERNAL_RETRY_QUEUE_INDEXES:
            cursor.execute(index_sql)

        for index_sql in EXTERNAL_API_ATTEMPTS_INDEXES:
            cursor.execute(index_sql)

        conn.commit()
        conn.close()
        logger.info("Resilience tables initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize resilience tables: {e}", exc_info=True)
        raise
