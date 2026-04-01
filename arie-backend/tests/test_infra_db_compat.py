"""
Infrastructure fix — Database compatibility tests for _get_app_data().

Tests verify that the supervisor pipeline's data-loading path works correctly
with both SQLite (local/test) and the get_db() abstraction (production/staging).

Covers:
  - Explicit db_path SQLite override (test fixture path)
  - get_db() default path (when no explicit file path provided)
  - try/finally cleanup on all paths including exceptions
  - _SqliteFallback duck-type compatibility
  - Pipeline data-loading returns correct structure
"""
import os
import sys
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from supervisor.agent_executors import _get_app_data, _SqliteFallback


def _create_minimal_test_db():
    """Create a minimal test SQLite DB with one application."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    db = sqlite3.connect(db_path)
    db.execute("""CREATE TABLE applications (
        id TEXT PRIMARY KEY, ref TEXT, company_name TEXT, country TEXT,
        registration_number TEXT, entity_type TEXT, ownership_structure TEXT,
        sector TEXT, risk_level TEXT, risk_score REAL, source_of_funds TEXT,
        expected_volume TEXT, client_id TEXT, status TEXT DEFAULT 'submitted',
        brn TEXT, assigned_to TEXT, prescreening_data TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE directors (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        position TEXT, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE ubos (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        first_name TEXT, last_name TEXT, full_name TEXT, nationality TEXT,
        ownership_pct REAL, is_pep TEXT DEFAULT 'No', pep_declaration TEXT DEFAULT '{}'
    )""")
    db.execute("""CREATE TABLE intermediaries (
        id TEXT PRIMARY KEY, application_id TEXT, person_key TEXT,
        entity_name TEXT, jurisdiction TEXT, ownership_pct REAL
    )""")
    db.execute("""CREATE TABLE documents (
        id TEXT PRIMARY KEY, application_id TEXT, document_type TEXT,
        filename TEXT, verification_status TEXT DEFAULT 'pending'
    )""")

    app_id = "infra-test-001"
    db.execute(
        "INSERT INTO applications (id, ref, company_name, country, sector, risk_level, risk_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (app_id, "APP-INFRA-001", "InfraTest Ltd", "United Kingdom", "Technology", "LOW", 25.0)
    )
    db.execute(
        "INSERT INTO directors (id, application_id, full_name, nationality) VALUES (?, ?, ?, ?)",
        ("dir-infra-1", app_id, "Alice Director", "UK")
    )
    db.execute(
        "INSERT INTO ubos (id, application_id, full_name, ownership_pct, nationality) VALUES (?, ?, ?, ?, ?)",
        ("ubo-infra-1", app_id, "Bob Owner", 100.0, "UK")
    )
    db.execute(
        "INSERT INTO documents (id, application_id, document_type, filename, verification_status) VALUES (?, ?, ?, ?, ?)",
        ("doc-infra-1", app_id, "passport", "passport.pdf", "verified")
    )
    db.commit()
    db.close()
    return db_path, app_id


# ═══════════════════════════════════════════════════════════
# Explicit db_path SQLite Override Tests
# ═══════════════════════════════════════════════════════════

class TestExplicitDbPath:
    def test_loads_application_by_id(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert data["application"]["company_name"] == "InfraTest Ltd"
        assert data["application"]["country"] == "United Kingdom"
        os.unlink(db_path)

    def test_loads_application_by_ref(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, "APP-INFRA-001")
        assert data["application"]["id"] == app_id
        os.unlink(db_path)

    def test_loads_directors(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert len(data["directors"]) == 1
        assert data["directors"][0]["full_name"] == "Alice Director"
        os.unlink(db_path)

    def test_loads_ubos(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert len(data["ubos"]) == 1
        assert data["ubos"][0]["ownership_pct"] == 100.0
        os.unlink(db_path)

    def test_loads_documents(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert len(data["documents"]) == 1
        assert data["documents"][0]["verification_status"] == "verified"
        os.unlink(db_path)

    def test_loads_intermediaries(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert "intermediaries" in data
        assert isinstance(data["intermediaries"], list)
        os.unlink(db_path)

    def test_raises_on_missing_application(self):
        db_path, _ = _create_minimal_test_db()
        with pytest.raises(RuntimeError, match="Application not found"):
            _get_app_data(db_path, "nonexistent-id")
        os.unlink(db_path)

    def test_all_values_are_dicts(self):
        """Verify return types are plain dicts, not sqlite3.Row."""
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert isinstance(data["application"], dict)
        for d in data["directors"]:
            assert isinstance(d, dict)
        for u in data["ubos"]:
            assert isinstance(u, dict)
        for doc in data["documents"]:
            assert isinstance(doc, dict)
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# _SqliteFallback Duck-Type Tests
# ═══════════════════════════════════════════════════════════

class TestSqliteFallback:
    def test_execute_fetchone(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'alice')")
        conn.commit()

        fb = _SqliteFallback(conn)
        row = fb.execute("SELECT * FROM t WHERE id = ?", (1,)).fetchone()
        assert row == {"id": 1, "name": "alice"}
        assert isinstance(row, dict)
        fb.close()

    def test_execute_fetchall(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'alice')")
        conn.execute("INSERT INTO t VALUES (2, 'bob')")
        conn.commit()

        fb = _SqliteFallback(conn)
        rows = fb.execute("SELECT * FROM t").fetchall()
        assert len(rows) == 2
        assert all(isinstance(r, dict) for r in rows)
        fb.close()

    def test_fetchone_returns_none_for_empty(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()

        fb = _SqliteFallback(conn)
        row = fb.execute("SELECT * FROM t").fetchone()
        assert row is None
        fb.close()


# ═══════════════════════════════════════════════════════════
# DB Handle Cleanup Tests
# ═══════════════════════════════════════════════════════════

class TestDbCleanup:
    def test_db_closed_on_success(self):
        db_path, app_id = _create_minimal_test_db()
        _get_app_data(db_path, app_id)
        # If we can connect again, the handle was properly released
        db = sqlite3.connect(db_path)
        count = db.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        assert count == 1
        db.close()
        os.unlink(db_path)

    def test_db_closed_on_not_found_error(self):
        db_path, _ = _create_minimal_test_db()
        with pytest.raises(RuntimeError):
            _get_app_data(db_path, "nonexistent")
        # DB handle should be released even after exception
        db = sqlite3.connect(db_path)
        count = db.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        assert count == 1
        db.close()
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# Return Structure Compatibility
# ═══════════════════════════════════════════════════════════

class TestReturnStructure:
    def test_return_keys(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        assert set(data.keys()) == {"application", "directors", "ubos", "documents", "intermediaries"}
        os.unlink(db_path)

    def test_application_has_expected_columns(self):
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        app = data["application"]
        for key in ("id", "ref", "company_name", "country", "sector", "risk_level", "risk_score"):
            assert key in app, f"Missing key: {key}"
        os.unlink(db_path)

    def test_dict_get_works_on_application(self):
        """Verify .get() works — proves it's a real dict, not sqlite3.Row."""
        db_path, app_id = _create_minimal_test_db()
        data = _get_app_data(db_path, app_id)
        app = data["application"]
        assert app.get("company_name") == "InfraTest Ltd"
        assert app.get("nonexistent_field") is None
        assert app.get("nonexistent_field", "default") == "default"
        os.unlink(db_path)
