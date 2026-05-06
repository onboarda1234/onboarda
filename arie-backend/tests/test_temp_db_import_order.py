"""Regression coverage for temp DB path import-order isolation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import these before the temp_db fixture runs. This reproduces the order that
# previously left module-level DB_PATH constants pointing at the default DB.
import config as imported_config  # noqa: E402
import db as imported_db  # noqa: E402
import server as imported_server  # noqa: E402


def test_temp_db_resyncs_already_imported_db_modules(temp_db):
    assert imported_config.DB_PATH == temp_db
    assert imported_db.DB_PATH == temp_db
    assert imported_server.DB_PATH == temp_db
    assert imported_server._CFG_DB_PATH == temp_db

    conn = imported_db.get_db()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
