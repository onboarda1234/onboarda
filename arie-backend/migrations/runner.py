"""
ARIE Finance — Database Migration Runner
=========================================
Automatically executes pending migrations at startup.
Tracks applied migrations in a schema_version table.
"""

import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("arie.migrations")

MIGRATIONS_DIR = Path(__file__).parent / "scripts"


def ensure_schema_version_table(db):
    """Create the schema_version tracking table if it doesn't exist."""
    db.execute("""
    CREATE TABLE IF NOT EXISTS schema_version (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT UNIQUE NOT NULL,
        filename TEXT NOT NULL,
        applied_at TEXT DEFAULT (datetime('now')),
        checksum TEXT
    )
    """)
    db.commit()


def get_applied_versions(db):
    """Return set of already-applied migration versions."""
    rows = db.execute("SELECT version FROM schema_version ORDER BY id").fetchall()
    return {row[0] if isinstance(row, tuple) else row["version"] for row in rows}


def get_pending_migrations(db):
    """Return list of (version, filepath) tuples for pending migrations, sorted."""
    applied = get_applied_versions(db)
    if not MIGRATIONS_DIR.exists():
        return []

    pending = []
    for f in sorted(MIGRATIONS_DIR.glob("migration_*.sql")):
        # Extract version from filename: migration_001_xxx.sql -> 001
        parts = f.stem.split("_", 2)
        if len(parts) >= 2:
            version = parts[1]
            if version not in applied:
                pending.append((version, f))
    return pending


def run_migration(db, version, filepath):
    """Execute a single migration file."""
    logger.info("Applying migration %s: %s", version, filepath.name)
    sql = filepath.read_text(encoding="utf-8")

    import hashlib
    checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

    try:
        db.executescript(sql)
        db.execute(
            "INSERT INTO schema_version (version, filename, checksum) VALUES (?, ?, ?)",
            (version, filepath.name, checksum)
        )
        db.commit()
        logger.info("✅ Migration %s applied successfully", version)
    except Exception as e:
        logger.error("❌ Migration %s failed: %s", version, e)
        raise


def run_all_migrations(db_path):
    """Run all pending database migrations. Call at startup after init_db()."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    try:
        ensure_schema_version_table(db)
        pending = get_pending_migrations(db)

        if not pending:
            logger.info("Database schema is up to date")
            return 0

        logger.info("Found %d pending migration(s)", len(pending))
        count = 0
        for version, filepath in pending:
            run_migration(db, version, filepath)
            count += 1

        logger.info("✅ Applied %d migration(s) successfully", count)
        return count
    finally:
        db.close()
