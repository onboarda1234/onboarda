"""Regression tests for EX-09: rule_engine no longer imports from server.py.

The root cause of EX-09 was:
  rule_engine.py → from server import get_application_parties → server.py
  re-imported as module "server" (not __main__) → module-level Prometheus
  metric registration runs twice → ValueError: Duplicated timeseries.

The fix moves get_application_parties() (and its PII helpers) into the
neutral party_utils.py module which has no Prometheus dependency.
"""

import ast
import importlib
import os
import sys
import types

import pytest


# ── Source-level checks ──────────────────────────────────────────

class TestNoServerImportInRuleEngine:
    """Verify rule_engine.py never imports from server.py."""

    def _get_source(self, module_name):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), f"{module_name}.py")
        with open(path) as f:
            return f.read()

    def test_rule_engine_does_not_import_from_server(self):
        """rule_engine.py must not contain 'from server import'."""
        source = self._get_source("rule_engine")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "server":
                names = [alias.name for alias in node.names]
                pytest.fail(
                    f"rule_engine.py still imports from server: "
                    f"'from server import {', '.join(names)}' at line {node.lineno}"
                )

    def test_party_utils_does_not_import_from_server(self):
        """party_utils.py must not import from server.py (would re-create the circular dep)."""
        source = self._get_source("party_utils")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "server":
                names = [alias.name for alias in node.names]
                pytest.fail(
                    f"party_utils.py imports from server: "
                    f"'from server import {', '.join(names)}' at line {node.lineno}"
                )


# ── Runtime import checks ───────────────────────────────────────

class TestPartyUtilsImportSafety:
    """Verify party_utils can be imported without triggering Prometheus issues."""

    def test_import_party_utils_standalone(self):
        """party_utils should import cleanly without server.py side effects."""
        import party_utils
        assert hasattr(party_utils, "get_application_parties")
        assert hasattr(party_utils, "decrypt_pii_fields")
        assert hasattr(party_utils, "encrypt_pii_fields")
        assert hasattr(party_utils, "PII_FIELDS_DIRECTORS")
        assert hasattr(party_utils, "PII_FIELDS_UBOS")

    def test_rule_engine_recompute_import_chain(self):
        """Importing recompute_risk should not pull in server.py."""
        from rule_engine import recompute_risk
        assert callable(recompute_risk)

    def test_party_utils_has_no_prometheus_dependency(self):
        """party_utils must not register any Prometheus metrics."""
        source_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "party_utils.py"
        )
        with open(source_path) as f:
            source = f.read()
        assert "prometheus_client" not in source, (
            "party_utils.py should not reference prometheus_client"
        )

    def test_get_application_parties_available_from_party_utils(self):
        """get_application_parties should be importable from party_utils."""
        from party_utils import get_application_parties
        assert callable(get_application_parties)

    def test_server_reexports_party_utils_functions(self):
        """server.py should still expose PII/party functions for backward compat."""
        from server import (
            get_application_parties,
            decrypt_pii_fields,
            encrypt_pii_fields,
            PII_FIELDS_DIRECTORS,
            PII_FIELDS_UBOS,
        )
        assert callable(get_application_parties)
        assert callable(decrypt_pii_fields)
        assert callable(encrypt_pii_fields)
        assert isinstance(PII_FIELDS_DIRECTORS, list)
        assert isinstance(PII_FIELDS_UBOS, list)


# ── Functional regression ────────────────────────────────────────

class TestPartyUtilsFunctional:
    """Verify party_utils functions behave identically to the old server.py versions."""

    def _make_db(self, app_id="test_pu_func"):
        """Create a minimal in-memory DB for testing party queries."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE directors (
                id INTEGER PRIMARY KEY,
                application_id TEXT,
                person_key TEXT,
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                full_name TEXT DEFAULT '',
                nationality TEXT DEFAULT '',
                is_pep TEXT DEFAULT 'No',
                pep_declaration TEXT DEFAULT '{}',
                date_of_birth TEXT DEFAULT '',
                passport_number TEXT DEFAULT '',
                id_number TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE ubos (
                id INTEGER PRIMARY KEY,
                application_id TEXT,
                person_key TEXT,
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                full_name TEXT DEFAULT '',
                nationality TEXT DEFAULT '',
                ownership_pct REAL DEFAULT 0,
                is_pep TEXT DEFAULT 'No',
                pep_declaration TEXT DEFAULT '{}',
                date_of_birth TEXT DEFAULT '',
                passport_number TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE intermediaries (
                id TEXT PRIMARY KEY,
                application_id TEXT,
                person_key TEXT,
                entity_name TEXT DEFAULT '',
                jurisdiction TEXT DEFAULT '',
                ownership_pct REAL DEFAULT 0
            )
        """)
        return conn

    def test_get_application_parties_returns_three_lists(self):
        """get_application_parties must return (directors, ubos, intermediaries)."""
        from party_utils import get_application_parties
        db = self._make_db()
        app_id = "test_app_1"
        db.execute(
            "INSERT INTO directors (application_id, person_key, first_name, last_name, full_name, nationality) "
            "VALUES (?,?,?,?,?,?)",
            (app_id, "d1", "Alice", "Smith", "Alice Smith", "Mauritius"),
        )
        db.execute(
            "INSERT INTO ubos (application_id, person_key, first_name, last_name, full_name, nationality, ownership_pct) "
            "VALUES (?,?,?,?,?,?,?)",
            (app_id, "u1", "Bob", "Jones", "Bob Jones", "UK", 50),
        )
        db.execute(
            "INSERT INTO intermediaries (id, application_id, person_key, entity_name, jurisdiction) "
            "VALUES (?,?,?,?,?)",
            ("int1", app_id, "i1", "HoldCo Ltd", "BVI"),
        )
        db.commit()

        dirs, ubos, ints = get_application_parties(db, app_id)
        assert len(dirs) == 1
        assert dirs[0]["full_name"] == "Alice Smith"
        assert dirs[0]["nationality"] == "Mauritius"
        assert len(ubos) == 1
        assert ubos[0]["full_name"] == "Bob Jones"
        assert len(ints) == 1
        assert ints[0]["full_name"] == "HoldCo Ltd"

    def test_hydrate_party_record_parses_pep_declaration(self):
        """hydrate_party_record should JSON-parse pep_declaration."""
        from party_utils import hydrate_party_record
        import json
        record = {
            "full_name": "Test Person",
            "pep_declaration": json.dumps({"public_function": "Minister"}),
        }
        result = hydrate_party_record(record)
        assert isinstance(result["pep_declaration"], dict)
        assert result["pep_declaration"]["public_function"] == "Minister"

    def test_encrypt_decrypt_roundtrip(self):
        """encrypt_pii_fields → decrypt_pii_fields roundtrip must be lossless."""
        from party_utils import encrypt_pii_fields, decrypt_pii_fields
        record = {"nationality": "Mauritius", "name": "Test"}
        encrypted = encrypt_pii_fields(record, ["nationality"])
        decrypted = decrypt_pii_fields(encrypted, ["nationality"])
        assert decrypted["nationality"] == "Mauritius"
        assert decrypted["name"] == "Test"

    def test_empty_application_returns_empty_lists(self):
        """No rows → three empty lists."""
        from party_utils import get_application_parties
        db = self._make_db()
        dirs, ubos, ints = get_application_parties(db, "nonexistent")
        assert dirs == []
        assert ubos == []
        assert ints == []
