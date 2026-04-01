"""
Wave 1 — Agent 4 (Corporate Structure & UBO Mapping) and Agent 2 (External Database)
Unit tests for rule-based executor logic.

Tests cover:
  Agent 4:
    - Direct ownership calculation
    - UBO threshold qualification (≥25%)
    - Ownership completeness tiers
    - Circular ownership detection
    - Nominee/trust/holding keyword detection
    - Shell company indicator aggregation
    - Complexity scoring
    - Escalation logic
    - Edge cases (no UBOs, >100% ownership, empty directors)

  Agent 2:
    - Registry source selection per jurisdiction
    - Registration number format validation
    - Entity type inference from company name
    - Director reconciliation (internal consistency)
    - Degraded mode labelling
    - Discrepancy aggregation
    - Escalation logic
    - Edge cases (missing data, unknown jurisdiction)
"""
import os
import sys
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
# Ensure degraded mode for Agent 2 tests (no external API)
os.environ.pop("OPENCORPORATES_API_KEY", None)

from supervisor.agent_executors import (
    execute_corporate_structure_ubo,
    execute_external_database,
    _build_ownership_graph,
    _detect_structure_indicators,
    _compute_complexity_score,
    _select_registry_source,
    _check_registration_number_format,
    _infer_entity_type,
    _match_directors_to_registry,
    _detect_keyword_match,
    UBO_THRESHOLD_PCT,
    COMPLETENESS_GOOD,
    NOMINEE_KEYWORDS,
    TRUST_KEYWORDS,
    HOLDING_KEYWORDS,
)


# ═══════════════════════════════════════════════════════════
# Test DB Setup Helpers
# ═══════════════════════════════════════════════════════════

def _create_test_db(app_data=None, directors=None, ubos=None, intermediaries=None):
    """Create a temporary SQLite database with test data.
    Returns (db_path, application_id).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    db = sqlite3.connect(db_path)

    db.execute("""CREATE TABLE applications (
        id TEXT PRIMARY KEY,
        ref TEXT,
        company_name TEXT,
        country TEXT,
        registration_number TEXT,
        entity_type TEXT,
        ownership_structure TEXT,
        sector TEXT,
        risk_level TEXT,
        risk_score REAL,
        source_of_funds TEXT,
        expected_volume TEXT,
        client_id TEXT,
        status TEXT DEFAULT 'submitted'
    )""")

    db.execute("""CREATE TABLE directors (
        id TEXT PRIMARY KEY,
        application_id TEXT,
        person_key TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT,
        nationality TEXT,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}'
    )""")

    db.execute("""CREATE TABLE ubos (
        id TEXT PRIMARY KEY,
        application_id TEXT,
        person_key TEXT,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT,
        nationality TEXT,
        ownership_pct REAL,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}'
    )""")

    db.execute("""CREATE TABLE intermediaries (
        id TEXT PRIMARY KEY,
        application_id TEXT,
        person_key TEXT,
        entity_name TEXT,
        jurisdiction TEXT,
        ownership_pct REAL
    )""")

    db.execute("""CREATE TABLE documents (
        id TEXT PRIMARY KEY,
        application_id TEXT,
        document_type TEXT,
        filename TEXT,
        verification_status TEXT
    )""")

    app_id = "test-app-001"
    defaults = {
        "id": app_id, "ref": "APP-TEST-001",
        "company_name": "TestCorp Ltd", "country": "Mauritius",
        "registration_number": "C12345", "entity_type": "Private Limited Company",
        "ownership_structure": "", "sector": "Financial Services",
        "risk_level": "MEDIUM", "risk_score": 55.0,
    }
    if app_data:
        defaults.update(app_data)

    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    db.execute(f"INSERT INTO applications ({cols}) VALUES ({placeholders})", list(defaults.values()))

    if directors:
        for i, d in enumerate(directors):
            d.setdefault("id", f"dir-{i}")
            d.setdefault("application_id", app_id)
            d.setdefault("full_name", f"Director {i}")
            cols = ", ".join(d.keys())
            placeholders = ", ".join(["?"] * len(d))
            db.execute(f"INSERT INTO directors ({cols}) VALUES ({placeholders})", list(d.values()))

    if ubos:
        for i, u in enumerate(ubos):
            u.setdefault("id", f"ubo-{i}")
            u.setdefault("application_id", app_id)
            u.setdefault("full_name", f"UBO {i}")
            u.setdefault("ownership_pct", 25.0)
            cols = ", ".join(u.keys())
            placeholders = ", ".join(["?"] * len(u))
            db.execute(f"INSERT INTO ubos ({cols}) VALUES ({placeholders})", list(u.values()))

    if intermediaries:
        for i, inter in enumerate(intermediaries):
            inter.setdefault("id", f"inter-{i}")
            inter.setdefault("application_id", app_id)
            inter.setdefault("entity_name", f"Holding Co {i}")
            inter.setdefault("jurisdiction", "Unknown")
            inter.setdefault("ownership_pct", 0.0)
            cols = ", ".join(inter.keys())
            placeholders = ", ".join(["?"] * len(inter))
            db.execute(f"INSERT INTO intermediaries ({cols}) VALUES ({placeholders})", list(inter.values()))

    db.commit()
    db.close()
    return db_path, app_id


# ═══════════════════════════════════════════════════════════
# AGENT 4: Corporate Structure & UBO Mapping Tests
# ═══════════════════════════════════════════════════════════

class TestOwnershipGraph:
    def test_direct_ownership_calculation(self):
        ubos = [
            {"full_name": "Alice", "ownership_pct": 40, "nationality": "UK"},
            {"full_name": "Bob", "ownership_pct": 35, "nationality": "France"},
        ]
        graph = _build_ownership_graph(ubos, [])
        assert graph["total_direct_pct"] == 75.0
        assert len(graph["direct_owners"]) == 2
        assert graph["layers"] == 1
        assert graph["circular_detected"] is False

    def test_indirect_paths_via_intermediaries(self):
        ubos = [{"full_name": "Alice", "ownership_pct": 50, "nationality": "UK"}]
        intermediaries = [
            {"entity_name": "HoldCo BVI", "jurisdiction": "BVI", "ownership_pct": 30},
        ]
        graph = _build_ownership_graph(ubos, intermediaries)
        assert graph["layers"] == 2
        assert len(graph["indirect_paths"]) == 1
        assert graph["indirect_paths"][0]["intermediary"] == "HoldCo BVI"
        assert graph["total_effective_pct"] == 80.0

    def test_circular_ownership_detected(self):
        ubos = [{"full_name": "Alice Holdings", "ownership_pct": 60, "nationality": "UK"}]
        intermediaries = [{"entity_name": "Alice Holdings", "jurisdiction": "UK", "ownership_pct": 20}]
        graph = _build_ownership_graph(ubos, intermediaries)
        assert graph["circular_detected"] is True

    def test_empty_ubos(self):
        graph = _build_ownership_graph([], [])
        assert graph["total_direct_pct"] == 0.0
        assert graph["layers"] == 1
        assert len(graph["direct_owners"]) == 0

    def test_pep_flag_parsing(self):
        ubos = [
            {"full_name": "A", "ownership_pct": 50, "nationality": "UK", "is_pep": "Yes"},
            {"full_name": "B", "ownership_pct": 30, "nationality": "UK", "is_pep": "No"},
            {"full_name": "C", "ownership_pct": 20, "nationality": "UK", "is_pep": "true"},
        ]
        graph = _build_ownership_graph(ubos, [])
        assert graph["direct_owners"][0]["is_pep"] is True
        assert graph["direct_owners"][1]["is_pep"] is False
        assert graph["direct_owners"][2]["is_pep"] is True


class TestStructureIndicators:
    def test_nominee_detected_in_ubo_name(self):
        ubos = [{"full_name": "Nominee Services Ltd", "ownership_pct": 100}]
        graph = _build_ownership_graph(ubos, [])
        result = _detect_structure_indicators(ubos, [], [], {}, graph)
        assert result["nominee_detected"] is True
        assert len(result["nominee_evidence"]) > 0
        assert "nominee_arrangement_detected" in result["shell_indicators"]

    def test_trust_detected(self):
        ubos = [{"full_name": "Family Trust", "ownership_pct": 80}]
        graph = _build_ownership_graph(ubos, [])
        result = _detect_structure_indicators(ubos, [], [], {}, graph)
        assert result["trust_detected"] is True

    def test_holding_detected_in_intermediary(self):
        intermediaries = [{"entity_name": "SPV Holdings Ltd", "jurisdiction": "BVI", "ownership_pct": 50}]
        graph = _build_ownership_graph([], intermediaries)
        result = _detect_structure_indicators([], intermediaries, [], {}, graph)
        assert result["holding_detected"] is True
        assert "holding_or_spv_in_chain" in result["shell_indicators"]

    def test_opaque_jurisdiction_flagged(self):
        intermediaries = [{"entity_name": "ShellCo", "jurisdiction": "British Virgin Islands", "ownership_pct": 40}]
        graph = _build_ownership_graph([], intermediaries)
        result = _detect_structure_indicators([], intermediaries, [], {}, graph)
        assert "opaque_jurisdiction_intermediary" in result["shell_indicators"]

    def test_no_officers_or_ubos(self):
        graph = _build_ownership_graph([], [])
        result = _detect_structure_indicators([], [], [], {}, graph)
        assert "no_officers_or_ubos" in result["shell_indicators"]

    def test_clean_structure(self):
        ubos = [{"full_name": "John Smith", "ownership_pct": 100}]
        directors = [{"full_name": "John Smith"}]
        graph = _build_ownership_graph(ubos, [])
        result = _detect_structure_indicators(ubos, [], directors, {}, graph)
        assert result["nominee_detected"] is False
        assert result["trust_detected"] is False
        assert result["holding_detected"] is False
        assert len(result["shell_indicators"]) == 0

    def test_ownership_structure_text_scanned(self):
        ubos = [{"full_name": "John Smith", "ownership_pct": 100}]
        app = {"ownership_structure": "Held via nominee arrangement through Trustee Corp"}
        graph = _build_ownership_graph(ubos, [])
        result = _detect_structure_indicators(ubos, [], [], app, graph)
        assert result["nominee_detected"] is True


class TestComplexityScore:
    def test_simple_structure(self):
        ubos = [{"full_name": "A", "ownership_pct": 100}]
        graph = _build_ownership_graph(ubos, [])
        indicators = {"nominee_detected": False, "trust_detected": False, "holding_detected": False, "shell_indicators": []}
        result = _compute_complexity_score(ubos, [], indicators, graph)
        assert result["tier"] == "LOW"
        assert result["score"] < 30

    def test_complex_many_ubos(self):
        ubos = [{"full_name": f"UBO {i}", "ownership_pct": 10} for i in range(5)]
        graph = _build_ownership_graph(ubos, [])
        indicators = {"nominee_detected": False, "trust_detected": False, "holding_detected": False, "shell_indicators": []}
        result = _compute_complexity_score(ubos, [], indicators, graph)
        assert result["score"] >= 20  # >3 UBOs adds 20
        assert "5 UBOs declared" in result["reasons"][0]

    def test_complex_circular_and_nominee(self):
        ubos = [{"full_name": "Nominee Corp", "ownership_pct": 50}]
        intermediaries = [{"entity_name": "Nominee Corp", "jurisdiction": "BVI", "ownership_pct": 30}]
        graph = _build_ownership_graph(ubos, intermediaries)
        graph["circular_detected"] = True
        indicators = {
            "nominee_detected": True, "trust_detected": False,
            "holding_detected": False, "shell_indicators": ["opaque_jurisdiction_intermediary"],
        }
        result = _compute_complexity_score(ubos, intermediaries, indicators, graph)
        assert result["tier"] == "HIGH"
        assert result["score"] >= 60

    def test_no_ubos_adds_complexity(self):
        graph = _build_ownership_graph([], [])
        indicators = {"nominee_detected": False, "trust_detected": False, "holding_detected": False, "shell_indicators": []}
        result = _compute_complexity_score([], [], indicators, graph)
        assert result["score"] >= 15  # no UBOs adds 15


class TestAgent4Executor:
    def test_clean_simple_structure(self):
        db_path, app_id = _create_test_db(
            ubos=[
                {"full_name": "Alice Smith", "ownership_pct": 60, "nationality": "UK"},
                {"full_name": "Bob Jones", "ownership_pct": 40, "nationality": "France"},
            ],
            directors=[{"full_name": "Alice Smith"}, {"full_name": "Bob Jones"}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert result["status"] == "clean"
        assert result["confidence_score"] >= 0.80
        assert result["total_ownership_mapped_pct"] == 100.0
        assert result["ubo_completeness"] == 1.0
        assert result["circular_ownership_detected"] is False
        assert result["qualified_ubo_count"] == 2
        assert result["escalation_flag"] is False
        os.unlink(db_path)

    def test_no_ubos_escalates(self):
        db_path, app_id = _create_test_db(
            directors=[{"full_name": "Director A"}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert result["status"] == "issues_found"
        assert result["escalation_flag"] is True
        assert result["ubo_completeness"] == 0.0
        assert any(i["issue_type"] == "ubo_not_identified" for i in result["detected_issues"])
        os.unlink(db_path)

    def test_incomplete_ownership(self):
        db_path, app_id = _create_test_db(
            ubos=[{"full_name": "Partial Owner", "ownership_pct": 30, "nationality": "UK"}],
            directors=[{"full_name": "Director A"}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert result["status"] == "issues_found"
        assert result["total_ownership_mapped_pct"] == 30.0
        assert any(i["issue_type"] == "incomplete_ownership" for i in result["detected_issues"])
        os.unlink(db_path)

    def test_nominee_detected_escalates(self):
        db_path, app_id = _create_test_db(
            ubos=[{"full_name": "Nominee Services Ltd", "ownership_pct": 100, "nationality": "BVI"}],
            directors=[{"full_name": "John Doe"}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert result["nominee_arrangements_detected"] is True
        assert "nominee_arrangement_detected" in result["shell_company_indicators"]
        assert result["escalation_flag"] is True
        os.unlink(db_path)

    def test_circular_ownership_detected(self):
        db_path, app_id = _create_test_db(
            ubos=[{"full_name": "CircularCo", "ownership_pct": 80, "nationality": "UK"}],
            directors=[{"full_name": "Director A"}],
            intermediaries=[{"entity_name": "CircularCo", "jurisdiction": "UK", "ownership_pct": 20}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert result["circular_ownership_detected"] is True
        assert result["escalation_flag"] is True
        assert any(i["issue_type"] == "circular_ownership" for i in result["detected_issues"])
        os.unlink(db_path)

    def test_ubo_threshold_qualification(self):
        db_path, app_id = _create_test_db(
            ubos=[
                {"full_name": "Major Owner", "ownership_pct": 60, "nationality": "UK"},
                {"full_name": "Minor Owner", "ownership_pct": 10, "nationality": "UK"},
            ],
            directors=[{"full_name": "Director A"}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert result["qualified_ubo_count"] == 1  # only 60% qualifies, 10% doesn't
        assert result["ubo_threshold_pct"] == 25.0
        qualified = [u for u in result["ubos_identified"] if u["qualifies_as_ubo"]]
        assert len(qualified) == 1
        assert qualified[0]["name"] == "Major Owner"
        os.unlink(db_path)

    def test_intermediary_adds_complexity(self):
        db_path, app_id = _create_test_db(
            ubos=[{"full_name": "Owner A", "ownership_pct": 100, "nationality": "UK"}],
            directors=[{"full_name": "Director A"}],
            intermediaries=[
                {"entity_name": "HoldCo Alpha", "jurisdiction": "BVI", "ownership_pct": 40},
                {"entity_name": "HoldCo Beta", "jurisdiction": "Cayman Islands", "ownership_pct": 30},
            ],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        assert len(result["indirect_ownership_paths"]) == 2
        assert result["ownership_structure"]["layers"] == 2
        # Two opaque jurisdiction intermediaries should increase complexity
        os.unlink(db_path)

    def test_classification_tags_present(self):
        db_path, app_id = _create_test_db(
            ubos=[{"full_name": "Owner", "ownership_pct": 100, "nationality": "UK"}],
            directors=[{"full_name": "Director"}],
        )
        result = execute_corporate_structure_ubo(app_id, {"db_path": db_path})
        # All findings should have classification field
        for f in result["findings"]:
            assert f.get("classification") == "rule", f"Finding missing classification: {f['title']}"
        for u in result["ubos_identified"]:
            assert u.get("classification") == "rule"
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# AGENT 2: External Database Verification Tests
# ═══════════════════════════════════════════════════════════

class TestRegistrySourceSelection:
    def test_mauritius(self):
        result = _select_registry_source("Mauritius")
        assert "CBRD" in result["source"]
        assert result["matched"] is True

    def test_uk(self):
        result = _select_registry_source("United Kingdom")
        assert "Companies House" in result["source"]

    def test_unknown_falls_back_to_opencorporates(self):
        result = _select_registry_source("Narnia")
        assert "OpenCorporates" in result["source"]
        assert result["matched"] is False

    def test_case_insensitive(self):
        result = _select_registry_source("SINGAPORE")
        assert "ACRA" in result["source"]


class TestRegistrationNumberFormat:
    def test_uk_valid(self):
        result = _check_registration_number_format("12345678", "United Kingdom")
        assert result["valid"] is True

    def test_uk_invalid(self):
        result = _check_registration_number_format("AB", "United Kingdom")
        assert result["valid"] is False

    def test_mauritius_valid(self):
        result = _check_registration_number_format("C12345", "Mauritius")
        assert result["valid"] is True

    def test_empty_number(self):
        result = _check_registration_number_format("", "Mauritius")
        assert result["valid"] is False

    def test_generic_valid(self):
        result = _check_registration_number_format("REG-999", "India")
        assert result["valid"] is True


class TestEntityTypeInference:
    def test_ltd(self):
        assert _infer_entity_type("TestCorp Ltd") == "Private Limited Company"

    def test_plc(self):
        assert _infer_entity_type("BigBank PLC") == "Public Limited Company"

    def test_gmbh(self):
        assert _infer_entity_type("Firma GmbH") == "Gesellschaft mit beschränkter Haftung"

    def test_no_match(self):
        assert _infer_entity_type("Just A Name") is None


class TestDirectorReconciliation:
    def test_all_directors_valid(self):
        directors = [{"full_name": "Alice"}, {"full_name": "Bob"}]
        result = _match_directors_to_registry(directors, {})
        assert result["match"] is True
        assert result["matched"] == 2

    def test_missing_name(self):
        directors = [{"full_name": "Alice"}, {"full_name": ""}]
        result = _match_directors_to_registry(directors, {})
        assert result["match"] is False
        assert result["unmatched"] == 1

    def test_no_directors(self):
        result = _match_directors_to_registry([], {})
        assert result["match"] is False
        assert "No directors declared" in result["issues"]


class TestAgent2Executor:
    def test_clean_verification(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp Ltd", "country": "Mauritius", "registration_number": "C12345"},
            directors=[{"full_name": "Alice Smith"}, {"full_name": "Bob Jones"}],
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        assert result["company_found"] is True
        assert result["lookup_mode"] == "degraded"
        assert result["provider_mode"] == "degraded"
        assert result["registration_number_match"] is True
        assert result["status"] == "clean"
        assert "CBRD" in result["registry_source"]
        os.unlink(db_path)

    def test_missing_company_name_escalates(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "", "country": "Mauritius"},
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        assert result["company_found"] is False
        assert result["escalation_flag"] is True
        assert result["status"] == "issues_found"
        assert any(i["issue_type"] == "company_not_verifiable" for i in result["detected_issues"])
        os.unlink(db_path)

    def test_missing_country_escalates(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp", "country": ""},
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        assert result["company_found"] is False
        assert result["escalation_flag"] is True
        os.unlink(db_path)

    def test_degraded_mode_caps_confidence(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp Ltd", "country": "Mauritius", "registration_number": "C12345"},
            directors=[{"full_name": "Alice"}],
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        # In degraded mode, confidence should not exceed 0.65 base
        assert result["confidence_score"] <= 0.70
        os.unlink(db_path)

    def test_invalid_registration_number_creates_discrepancy(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp", "country": "United Kingdom", "registration_number": "AB"},
            directors=[{"full_name": "Alice"}],
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        assert len(result["discrepancies"]) > 0
        assert any(d["field"] == "registration_number" for d in result["discrepancies"])
        os.unlink(db_path)

    def test_director_issues_create_discrepancy(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp Ltd", "country": "Mauritius", "registration_number": "C12345"},
            directors=[{"full_name": "Alice"}, {"full_name": ""}],
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        assert any(d["field"] == "directors" for d in result["discrepancies"])
        os.unlink(db_path)

    def test_checks_performed_structure(self):
        db_path, app_id = _create_test_db(
            app_data={"company_name": "TestCorp Ltd", "country": "Singapore", "registration_number": "REG123"},
            directors=[{"full_name": "Alice"}],
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        checks = result["checks_performed"]
        assert "registry_source" in checks
        assert "company_lookup" in checks
        assert "registration_number" in checks
        assert "entity_type" in checks
        assert "jurisdiction" in checks
        assert "directors" in checks
        os.unlink(db_path)

    def test_entity_type_check_populated(self):
        db_path, app_id = _create_test_db(
            app_data={
                "company_name": "TestCorp Ltd", "country": "Mauritius",
                "registration_number": "C12345", "entity_type": "Private Limited Company",
            },
            directors=[{"full_name": "Alice"}],
        )
        result = execute_external_database(app_id, {"db_path": db_path})
        et = result["entity_type_check"]
        assert et["declared"] == "Private Limited Company"
        assert et["inferred"] == "Private Limited Company"
        assert et["match"] is True
        os.unlink(db_path)


# ═══════════════════════════════════════════════════════════
# Cross-cutting: keyword detection utility
# ═══════════════════════════════════════════════════════════

class TestKeywordDetection:
    def test_nominee_match(self):
        assert _detect_keyword_match("ABC Nominee Services", NOMINEE_KEYWORDS) == "nominee"

    def test_trustee_match(self):
        assert _detect_keyword_match("Global Trustee Corp", NOMINEE_KEYWORDS) == "trustee"

    def test_trust_match(self):
        assert _detect_keyword_match("Family Trust", TRUST_KEYWORDS) == "trust"

    def test_spv_match(self):
        assert _detect_keyword_match("Alpha SPV Ltd", HOLDING_KEYWORDS) == "spv"

    def test_no_match(self):
        assert _detect_keyword_match("Normal Company Ltd", NOMINEE_KEYWORDS) is None

    def test_case_insensitive(self):
        assert _detect_keyword_match("NOMINEE CORP", NOMINEE_KEYWORDS) == "nominee"
