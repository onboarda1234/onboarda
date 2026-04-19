"""
ARIE Finance — Database Migration Runner
=========================================
Automatically executes pending migrations at startup.
Tracks applied migrations in a schema_version table.
Supports both SQLite (development) and PostgreSQL (production).
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("arie.migrations")

MIGRATIONS_DIR = Path(__file__).parent / "scripts"


def _get_schema_version_ddl(is_postgres: bool) -> str:
    """Return DDL for the schema_version tracking table."""
    if is_postgres:
        return """
        CREATE TABLE IF NOT EXISTS schema_version (
            id SERIAL PRIMARY KEY,
            version TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            description TEXT DEFAULT '',
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum TEXT
        )
        """
    else:
        return """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            description TEXT DEFAULT '',
            applied_at TEXT DEFAULT (datetime('now')),
            checksum TEXT
        )
        """


def ensure_schema_version_table(db):
    """Create the schema_version tracking table if it doesn't exist."""
    is_postgres = getattr(db, 'is_postgres', False)
    ddl = _get_schema_version_ddl(is_postgres)
    if is_postgres:
        # Use execute for PostgreSQL (executescript uses cursor.execute for PG)
        cursor = db._cursor_or_create()
        cursor.execute(ddl)
    else:
        db.executescript(ddl)
    db.commit()

    # Migration: add description column if missing (for older schema_version tables)
    try:
        db.execute("SELECT description FROM schema_version LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE schema_version ADD COLUMN description TEXT DEFAULT ''")
            db.commit()
        except Exception:
            pass  # Column may already exist


def get_applied_versions(db):
    """Return set of already-applied migration versions."""
    rows = db.execute("SELECT version FROM schema_version ORDER BY id").fetchall()
    return {row["version"] if isinstance(row, dict) else row[0] for row in rows}


def get_pending_migrations(db):
    """Return list of (version, filepath, description) tuples for pending migrations, sorted."""
    applied = get_applied_versions(db)
    if not MIGRATIONS_DIR.exists():
        return []

    pending = []
    for f in sorted(MIGRATIONS_DIR.glob("migration_*.sql")):
        # Extract version from filename: migration_001_xxx.sql -> 001
        parts = f.stem.split("_", 2)
        if len(parts) >= 2:
            version = parts[1]
            description = parts[2].replace("_", " ") if len(parts) > 2 else ""
            if version not in applied:
                pending.append((version, f, description))
    return pending


def run_migration(db, version, filepath, description=""):
    """Execute a single migration file."""
    logger.info("Applying migration %s: %s", version, filepath.name)
    sql = filepath.read_text(encoding="utf-8")

    checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

    try:
        db.executescript(sql)
        db.execute(
            "INSERT INTO schema_version (version, filename, description, checksum) VALUES (?, ?, ?, ?)",
            (version, filepath.name, description, checksum)
        )
        db.commit()
        logger.info("Migration %s applied successfully", version)
    except Exception as e:
        logger.error("Migration %s failed: %s", version, e)
        raise


def run_all_migrations_with_connection(db):
    """
    Run all pending database migrations using a DBConnection instance.
    Works with both SQLite and PostgreSQL via the DBConnection abstraction.
    """
    ensure_schema_version_table(db)
    pending = get_pending_migrations(db)

    if not pending:
        logger.info("Database schema is up to date")
        return 0

    logger.info("Found %d pending migration(s)", len(pending))
    count = 0
    for version, filepath, description in pending:
        run_migration(db, version, filepath, description)
        count += 1

    logger.info("Applied %d migration(s) successfully", count)
    return count


def run_all_migrations(db_path=None):
    """
    Run all pending database migrations.
    If db_path is provided, uses SQLite directly (backward compatible).
    Otherwise, uses the DBConnection abstraction from db module.
    """
    if db_path:
        # Legacy SQLite-only path (backward compatible)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Wrap in a minimal interface for compatibility
            from db import DBConnection
            db = DBConnection(conn, is_postgres=False)
            return run_all_migrations_with_connection(db)
        finally:
            conn.close()
    else:
        # Use DBConnection abstraction (supports both SQLite and PostgreSQL)
        from db import get_db
        db = get_db()
        try:
            return run_all_migrations_with_connection(db)
        finally:
            db.close()


def get_migration_status():
    """
    Return migration status information.
    Returns: { applied: [...], pending: [...], current_version: str }
    """
    from db import get_db
    db = get_db()
    try:
        ensure_schema_version_table(db)

        # Get applied migrations
        applied_rows = db.execute(
            "SELECT version, filename, description, applied_at, checksum FROM schema_version ORDER BY id"
        ).fetchall()

        applied = []
        for row in applied_rows:
            applied.append({
                "version": row["version"],
                "filename": row["filename"],
                "description": row.get("description", ""),
                "applied_at": row["applied_at"],
                "checksum": row.get("checksum", ""),
            })

        # Get pending migrations
        pending = get_pending_migrations(db)
        pending_list = [
            {"version": v, "filename": f.name, "description": d}
            for v, f, d in pending
        ]

        # Current version
        current = applied[-1]["version"] if applied else "none"

        return {
            "applied": applied,
            "pending": pending_list,
            "current_version": current,
            "total_applied": len(applied),
            "total_pending": len(pending_list),
        }
    finally:
        db.close()
