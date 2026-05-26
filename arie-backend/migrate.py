#!/usr/bin/env python3
"""
ONBOARDA — Database Migration CLI
====================================
Manages schema migrations for both SQLite (dev) and PostgreSQL (production).

Usage:
    python migrate.py status    Show applied and pending migrations
    python migrate.py up        Apply all pending migrations
    python migrate.py version   Show current schema version
    python migrate.py pending   List only pending migrations

Environment:
    DATABASE_URL    If set, connects to PostgreSQL (production)
    DB_PATH         SQLite file path (development, default: ./arie.db)
"""

import os
import sys
import logging

# Ensure we can import from the backend directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set defaults for required env vars if not present
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "migration-cli-key")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("arie.migrate")


def cmd_status():
    """Show full migration status."""
    from migrations.runner import get_migration_status
    status = get_migration_status()

    print("\n  ONBOARDA — Migration Status")
    print("  " + "=" * 50)

    from db import USE_POSTGRESQL
    db_type = "PostgreSQL" if USE_POSTGRESQL else "SQLite"
    print(f"  Database: {db_type}")
    print(f"  Current version: {status['current_version']}")
    print(f"  Applied: {status['total_applied']}  |  Pending: {status['total_pending']}")
    print()

    if status["applied"]:
        print("  Applied migrations:")
        for m in status["applied"]:
            desc = m.get("description", "") or m["filename"]
            print(f"    v{m['version']}  {desc}  (applied {m['applied_at']})")
    else:
        print("  No migrations applied yet.")

    if status["pending"]:
        print()
        print("  Pending migrations:")
        for m in status["pending"]:
            desc = m.get("description", "") or m["filename"]
            print(f"    v{m['version']}  {desc}")
    else:
        print("  All migrations are up to date.")

    print()


def cmd_up():
    """Apply all pending migrations."""
    from migrations.runner import run_all_migrations
    print("\n  Applying pending migrations...")
    count = run_all_migrations()
    if count > 0:
        print(f"  Applied {count} migration(s) successfully.\n")
    else:
        print("  No pending migrations to apply.\n")


def cmd_version():
    """Show current schema version."""
    from migrations.runner import get_migration_status
    status = get_migration_status()
    print(f"  Current schema version: {status['current_version']}")
    print(f"  Total applied: {status['total_applied']}")


def cmd_pending():
    """List only pending migrations."""
    from migrations.runner import get_migration_status
    status = get_migration_status()
    if status["pending"]:
        print(f"\n  {status['total_pending']} pending migration(s):")
        for m in status["pending"]:
            desc = m.get("description", "") or m["filename"]
            print(f"    v{m['version']}  {desc}")
        print()
    else:
        print("  No pending migrations.\n")


def main():
    commands = {
        "status": cmd_status,
        "up": cmd_up,
        "version": cmd_version,
        "pending": cmd_pending,
    }

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)

    if cmd not in commands:
        print(f"  Unknown command: {cmd}")
        print(f"  Available commands: {', '.join(commands.keys())}")
        sys.exit(1)

    try:
        commands[cmd]()
    except Exception as e:
        logger.error(f"Migration command '{cmd}' failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
