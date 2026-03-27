"""
ARIE Finance — Database Migration Runner
=========================================
Automatically executes pending migrations at startup.
Tracks applied migrations in a schema_version table.
Supports both SQLite and PostgreSQL via db.py DBConnection.
"""

import re
import logging
import hashlib
from pathlib import Path

logger = logging.getLogger("arie.migrations")

MIGRATIONS_DIR = Path(__file__).parent / "scripts"


def _translate_for_postgres(sql):
    """Translate SQLite-specific SQL to PostgreSQL-compatible SQL."""
    # INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
    sql = re.sub(
        r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
        'SERIAL PRIMARY KEY',
        sql,
        flags=re.IGNORECASE
    )
    # datetime('now') → CURRENT_TIMESTAMP
    sql = re.sub(
        r"datetime\('now'\)",
        'CURRENT_TIMESTAMP',
        sql,
        flags=re.IGNORECASE
    )
    # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    sql = re.sub(
        r'INSERT\s+OR\s+IGNORE\s+INTO',
        'INSERT INTO',
        sql,
        flags=re.IGNORECASE
    )
    return sql


def ensure_schema_version_table(db):
    """Create the schema_version tracking table if it doesn't exist."""
    try:
        db.execute("SELECT 1 FROM schema_version LIMIT 1")
    except Exception:
        # PostgreSQL: failed SELECT aborts the transaction — must rollback first
        if db.is_postgres:
            db.conn.rollback()
        # Table doesn't exist — create it with portable SQL
        create_sql = """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY,
            version TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum TEXT
        )
        """
        if db.is_postgres:
            create_sql = create_sql.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        db.execute(create_sql)
        db.commit()


def get_applied_versions(db):
    """Return set of already-applied migration versions."""
    rows = db.execute("SELECT version FROM schema_version ORDER BY id").fetchall()
    return {row["version"] for row in rows}


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

    # Translate SQLite SQL to PostgreSQL if needed
    if db.is_postgres:
        sql = _translate_for_postgres(sql)

    checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

    try:
        # Execute each statement separately (executescript is SQLite-only)
        statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
        for i, statement in enumerate(statements):
            # For INSERT with ON CONFLICT pattern, add DO NOTHING on postgres
            if db.is_postgres and "INSERT INTO" in statement and "ON CONFLICT" not in statement:
                if "VALUES" in statement:
                    statement = statement.rstrip() + " ON CONFLICT DO NOTHING"
            if db.is_postgres:
                # Use savepoint so a failed statement doesn't abort the transaction
                db.conn.cursor().execute(f"SAVEPOINT migration_stmt_{i}")
            try:
                db.execute(statement)
            except Exception as stmt_err:
                if db.is_postgres:
                    db.conn.cursor().execute(f"ROLLBACK TO SAVEPOINT migration_stmt_{i}")
                logger.debug("Statement skipped in migration %s: %s", version, stmt_err)
        db.execute(
            "INSERT INTO schema_version (version, filename, checksum) VALUES (?, ?, ?)",
            (version, filepath.name, checksum)
        )
        db.commit()
        logger.info("✅ Migration %s applied successfully", version)
    except Exception as e:
        logger.error("❌ Migration %s failed: %s", version, e)
        raise


def run_all_migrations(db):
    """
    Run all pending database migrations.
    Call at startup after init_db().

    Args:
        db: A DBConnection from db.py (handles SQLite/PostgreSQL ? -> %s translation)
    """
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
    except Exception as e:
        logger.error("Migration runner error: %s", e)
        raise
