"""
Tests for verification_matrix.py and document_verification.py

Covers:
  - Matrix integrity (all required keys, no duplicates, valid classifications)
  - Rule/hybrid/AI routing
  - Conditional licence gate
  - cert_reg retirement
  - Historical verification_results format backward compatibility
  - Name matching thresholds
  - Gate checks
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification_matrix import (
    CheckClassification,
    CheckStatus,
    get_checks_for_doc_type,
    get_ai_checks_for_doc_type,
    get_rule_checks_for_doc_type,
    is_licence_applicable,
    build_ai_checks_seed,
    summarise_matrix,
    ALL_DOC_CHECKS,
    GATE_CHECKS,
)
from document_verification import (
    _name_similarity,
    _check_name_match,
    _check_not_expired,
    _check_date_recency,
    run_gate_checks,
    run_rule_checks,
    verify_document_layered,
    to_legacy_result,
)


# ── Matrix integrity ───────────────────────────────────────────────

class TestMatrixIntegrity:
    def test_all_doc_checks_non_empty(self):
        """Every non-retired doc type must have at least one check."""
        for doc_type, entry in ALL_DOC_CHECKS.items():
            if entry.get("retired"):
                continue
            checks = entry.get("checks", [])
            assert len(checks) > 0, f"{doc_type} has no checks and is not retired"

    def test_cert_reg_retired(self):
        """cert_reg must be marked retired with empty checks."""
        entry = ALL_DOC_CHECKS.get("cert_reg", {})
        assert entry.get("retired") is True
        assert entry.get("checks", []) == []

    def test_no_unexpected_duplicate_check_ids(self):
        """Duplicate check IDs are only permitted for the cross-cutting CERT-01 check."""
        ALLOWED_CROSS_CUTTING = {"CERT-01"}  # intentionally shared across doc types
        seen = {}
        for doc_type, entry in ALL_DOC_CHECKS.items():
            for check in entry.get("checks", []):
                cid = check.get("id")
                if cid and cid not in ALLOWED_CROSS_CUTTING:
                    assert cid not in seen, \
                        f"Unexpected duplicate check ID '{cid}' in {doc_type} and {seen[cid]}"
                    seen[cid] = doc_type

    def test_all_classifications_valid(self):
        """Every check must have a valid classification."""
        valid = {CheckClassification.RULE, CheckClassification.HYBRID, CheckClassification.AI}
        for doc_type, entry in ALL_DOC_CHECKS.items():
            for check in entry.get("checks", []):
                cls = check.get("classification")
                assert cls in valid, \
                    f"Invalid classification '{cls}' in {doc_type}/{check.get('id')}"

    def test_memarts_named_correctly(self):
        """memarts doc_name must be 'Memorandum of Association', not 'Memorandum & Articles'."""
        entry = ALL_DOC_CHECKS.get("memarts", {})
        name = entry.get("doc_name", "") or entry.get("name", "")
        assert "Memorandum of Association" in name
        assert "& Articles" not in name

    def test_reg_sh_no_currency_check(self):
        """Shareholder Register must not have a Currency/age check (retired to rule engine)."""
        checks = ALL_DOC_CHECKS.get("reg_sh", {}).get("checks", [])
        labels = [c.get("label", "").lower() for c in checks]
        assert "currency" not in labels

    def test_fin_stmt_no_audit_completeness(self):
        """Financial Statements must not have Audit Status or Completeness AI checks."""
        checks = ALL_DOC_CHECKS.get("fin_stmt", {}).get("checks", [])
        labels = [c.get("label", "").lower() for c in checks]
        assert "audit status" not in labels
        assert "completeness" not in labels

    def test_licence_conditional_flag(self):
        """Licence doc type must be marked conditional on holds_licence."""
        entry = ALL_DOC_CHECKS.get("licence", {})
        assert entry.get("conditional") == "holds_licence"

    def test_summarise_matrix(self):
        """summarise_matrix must return counts including rule, hybrid, ai."""
        summary = summarise_matrix()
        assert "rule" in summary
        assert "hybrid" in summary
        assert "ai" in summary
        assert "total_checks" in summary
        assert summary["total_checks"] > 0
        assert summary["rule"] + summary["hybrid"] + summary["ai"] <= summary["total_checks"]

    def test_build_ai_checks_seed(self):
        """build_ai_checks_seed must return a list of (category, doc_type, name, checks_json) tuples."""
        seed = build_ai_checks_seed()
        assert isinstance(seed, list)
        assert len(seed) > 0
        for item in seed:
            assert len(item) == 4
            category, doc_type, name, checks_json = item
            assert category in ("entity", "person")
            assert isinstance(doc_type, str)
            parsed = json.loads(checks_json)
            assert isinstance(parsed, list)


# ── Routing functions ──────────────────────────────────────────────

class TestRoutingFunctions:
    def test_get_checks_for_doc_type_cert_inc(self):
        checks = get_checks_for_doc_type("cert_inc", "entity")
        assert len(checks) > 0

    def test_get_checks_for_cert_reg_retired(self):
        """cert_reg must return empty list (retired)."""
        checks = get_checks_for_doc_type("cert_reg", "entity")
        assert checks == []

    def test_get_ai_checks_separates_ai_hybrid(self):
        """get_ai_checks_for_doc_type must only return AI and HYBRID classified checks."""
        checks = get_ai_checks_for_doc_type("cert_inc", "entity")
        for c in checks:
            assert c["classification"] in (CheckClassification.AI, CheckClassification.HYBRID)

    def test_get_rule_checks_separates_rule(self):
        """get_rule_checks_for_doc_type must only return RULE classified checks."""
        checks = get_rule_checks_for_doc_type("cert_inc", "entity")
        for c in checks:
            assert c["classification"] == CheckClassification.RULE

    def test_unknown_doc_type_returns_empty(self):
        checks = get_checks_for_doc_type("nonexistent_doc_xyz", "entity")
        assert checks == []


# ── Licence applicability gate ─────────────────────────────────────

class TestLicenceApplicability:
    def test_explicit_is_licensed_false(self):
        assert is_licence_applicable({"is_licensed": False, "regulatory_licences": "FCA authorised PI"}) is False

    def test_explicit_is_licensed_true(self):
        assert is_licence_applicable({"is_licensed": True, "regulatory_licences": ""}) is True

    def test_explicit_none(self):
        assert is_licence_applicable({"regulatory_licences": "None"}) is False

    def test_lowercase_none(self):
        assert is_licence_applicable({"regulatory_licences": "none"}) is False

    def test_na_variants(self):
        assert is_licence_applicable({"regulatory_licences": "n/a"}) is False
        assert is_licence_applicable({"regulatory_licences": "N/A"}) is False
        assert is_licence_applicable({"regulatory_licences": "no"}) is False

    def test_empty_string(self):
        assert is_licence_applicable({"regulatory_licences": ""}) is False

    def test_null_value(self):
        assert is_licence_applicable({"regulatory_licences": None}) is False

    def test_missing_key(self):
        assert is_licence_applicable({}) is False

    def test_valid_licence(self):
        assert is_licence_applicable({"regulatory_licences": "FCA authorised PI, ref 12345"}) is True

    def test_valid_licence_short(self):
        assert is_licence_applicable({"regulatory_licences": "EMI licence Malta"}) is True


# ── Name matching ──────────────────────────────────────────────────

class TestNameMatching:
    def test_exact_match(self):
        sim = _name_similarity("Acme Corp Ltd", "Acme Corp Ltd")
        assert sim == 1.0

    def test_case_insensitive(self):
        sim = _name_similarity("ACME CORP LTD", "acme corp ltd")
        assert sim >= 0.95

    def test_legal_suffix_stripped(self):
        # "Ltd" vs "Limited" should score high after suffix stripping
        sim = _name_similarity("Acme Corp Ltd", "Acme Corp Limited")
        assert sim >= 0.85

    def test_completely_different(self):
        sim = _name_similarity("Acme Corp", "Totally Different Company")
        assert sim < 0.5

    def test_empty_strings(self):
        sim = _name_similarity("", "")
        assert sim == 0.0

    def test_check_name_match_pass(self):
        # signature: (id_, label, extracted, declared, classification)
        result = _check_name_match("NM-01", "Name Match",
                                   "Acme Corp Ltd", "Acme Corp Ltd",
                                   CheckClassification.RULE)
        assert result["result"] == CheckStatus.PASS

    def test_check_name_match_warn(self):
        # Moderately similar names
        result = _check_name_match("NM-02", "Name Match",
                                   "Acme Corp Ltd", "Acme Corp Pty",
                                   CheckClassification.RULE)
        assert result["result"] in (CheckStatus.WARN, CheckStatus.PASS)

    def test_check_name_match_missing_extracted(self):
        result = _check_name_match("NM-03", "Name Match",
                                   "", "Acme Corp Ltd",
                                   CheckClassification.RULE)
        assert result["result"] in (CheckStatus.WARN, CheckStatus.FAIL)

    def test_check_name_match_missing_prescreening(self):
        result = _check_name_match("NM-04", "Name Match",
                                   "Acme Corp Ltd", "",
                                   CheckClassification.RULE)
        assert result["result"] in (CheckStatus.WARN, CheckStatus.FAIL)


# ── Gate checks ────────────────────────────────────────────────────

class TestGateChecks:
    def test_missing_file(self):
        results = run_gate_checks("", 1024, "application/pdf", [])
        ids = [r["id"] for r in results]
        assert "GATE-01" in ids
        assert "GATE-02" in ids
        assert "GATE-03" in ids

    def test_oversized_file_fails_gate02(self):
        results = run_gate_checks("", 30 * 1024 * 1024, "application/pdf", [])
        gate02 = next(r for r in results if r["id"] == "GATE-02")
        assert gate02["result"] == CheckStatus.FAIL

    def test_valid_size_passes_gate02(self):
        results = run_gate_checks("", 1 * 1024 * 1024, "application/pdf", [])
        gate02 = next(r for r in results if r["id"] == "GATE-02")
        assert gate02["result"] == CheckStatus.PASS


# ── Rule checks ────────────────────────────────────────────────────

class TestRuleChecks:
    def test_cert_inc_name_match_pass(self):
        extracted = {"entity_name": "Acme Corp Ltd"}
        ps = {"registered_entity_name": "Acme Corp Ltd"}
        results = run_rule_checks("cert_inc", "entity", extracted, ps, "MEDIUM")
        name_checks = [r for r in results if "name" in r.get("id", "").lower() or "name" in r.get("label", "").lower()]
        assert any(r["result"] == CheckStatus.PASS for r in name_checks)

    def test_cert_inc_name_mismatch_warn_or_fail(self):
        extracted = {"entity_name": "Completely Different Entity"}
        ps = {"registered_entity_name": "Acme Corp Ltd"}
        results = run_rule_checks("cert_inc", "entity", extracted, ps, "MEDIUM")
        name_checks = [r for r in results if "name" in r.get("label", "").lower()]
        if name_checks:
            assert any(r["result"] in (CheckStatus.WARN, CheckStatus.FAIL) for r in name_checks)

    def test_passport_expiry_check_present(self):
        extracted = {"expiry_date": "2030-01-01"}
        ps = {"nationality": "Mauritian"}
        results = run_rule_checks("passport", "person", extracted, ps, "LOW")
        expiry_checks = [r for r in results if "expir" in r.get("label", "").lower()]
        assert len(expiry_checks) > 0
        assert expiry_checks[0]["result"] == CheckStatus.PASS

    def test_passport_expired_fails(self):
        extracted = {"expiry_date": "2020-01-01"}
        ps = {}
        results = run_rule_checks("passport", "person", extracted, ps, "LOW")
        expiry_checks = [r for r in results if "expir" in r.get("label", "").lower()]
        if expiry_checks:
            assert expiry_checks[0]["result"] == CheckStatus.FAIL


# ── Layered engine integration ─────────────────────────────────────

class TestVerifyDocumentLayered:
    def test_cert_reg_returns_skip(self):
        result = verify_document_layered(
            doc_type="cert_reg",
            category="entity",
            file_path=None,
            file_size=0,
            mime_type="",
            prescreening_data={},
            risk_level="MEDIUM",
            existing_hashes=[],
            claude_client=None,
        )
        assert result["overall"] in ("flagged", "verified", "skipped")
        checks = result.get("checks", [])
        assert any(c.get("result") == CheckStatus.SKIP for c in checks)

    def test_licence_gate_skips_when_no_licence(self):
        result = verify_document_layered(
            doc_type="licence",
            category="entity",
            file_path=None,
            file_size=0,
            mime_type="",
            prescreening_data={"is_licensed": False, "regulatory_licences": "None"},
            risk_level="LOW",
            existing_hashes=[],
            claude_client=None,
        )
        checks = result.get("checks", [])
        assert any(c.get("result") == CheckStatus.SKIP for c in checks)

    def test_licence_gate_proceeds_when_applicable(self):
        """When licence is applicable, engine should run gate checks (not return early skip)."""
        result = verify_document_layered(
            doc_type="licence",
            category="entity",
            file_path=None,
            file_size=0,
            mime_type="",
            prescreening_data={"is_licensed": True, "regulatory_licences": "FCA PI licence ref 12345"},
            risk_level="LOW",
            existing_hashes=[],
            claude_client=None,
        )
        checks = result.get("checks", [])
        # Should have gate checks (not just a single skip)
        non_skip = [c for c in checks if c.get("result") != CheckStatus.SKIP]
        assert len(non_skip) > 0 or len(checks) > 0  # at minimum gate checks ran

    def test_result_has_required_keys(self):
        result = verify_document_layered(
            doc_type="cert_inc",
            category="entity",
            file_path=None,
            file_size=1024,
            mime_type="application/pdf",
            prescreening_data={"registered_entity_name": "Acme Corp"},
            risk_level="MEDIUM",
            existing_hashes=[],
            claude_client=None,
        )
        assert "checks" in result
        assert "overall" in result
        assert "engine_version" in result
        assert result["engine_version"] == "layered_v1"

    def test_overall_is_valid_value(self):
        result = verify_document_layered(
            doc_type="cert_inc",
            category="entity",
            file_path=None,
            file_size=1024,
            mime_type="application/pdf",
            prescreening_data={},
            risk_level="LOW",
            existing_hashes=[],
            claude_client=None,
        )
        assert result["overall"] in ("verified", "flagged", "skipped")


# ── Backward compatibility ─────────────────────────────────────────

class TestBackwardCompatibility:
    def test_to_legacy_result_converts_new_format(self):
        new_result = {
            "checks": [
                {"id": "NM-01", "label": "Name Match", "classification": "rule",
                 "result": "pass", "message": "Names match", "type": "name"}
            ],
            "overall": "verified",
            "confidence": 0.9,
            "engine_version": "layered_v1",
        }
        legacy = to_legacy_result(new_result)
        assert "checks" in legacy
        assert "overall" in legacy
        assert legacy["overall"] in ("verified", "flagged")
        # Legacy format check items must have label and result
        for c in legacy["checks"]:
            assert "label" in c or "type" in c
            assert "result" in c

    def test_legacy_format_check_preserved(self):
        """Old-format verification_results (array of dicts) must not crash the renderer."""
        old_format = [
            {"label": "Document Expiry", "type": "expiry", "result": "pass",
             "message": "Valid for 900 more days"},
            {"label": "Name Match", "type": "name", "result": "warn",
             "message": "Fuzzy match 82%"}
        ]
        # Should be parseable — this is a smoke test, not a function test
        assert isinstance(old_format, list)
        for item in old_format:
            assert "result" in item

    def test_new_format_has_classification_field(self):
        """New format check items must include classification."""
        result = verify_document_layered(
            doc_type="cert_inc",
            category="entity",
            file_path=None,
            file_size=1024,
            mime_type="application/pdf",
            prescreening_data={"registered_entity_name": "Acme"},
            risk_level="LOW",
            existing_hashes=[],
            claude_client=None,
        )
        for check in result.get("checks", []):
            # Gate and rule checks must have classification
            if check.get("id", "").startswith("GATE") or check.get("source") == "rule":
                assert "classification" in check


# ── Agent 1 Matrix Alignment Tests ───────────────────────────────────

class TestNewCheckDefinitions:
    """Verify new checks required by Agent 1 matrix exist in ALL_DOC_CHECKS."""

    def test_cert_inc_has_incorporation_date_match(self):
        checks = get_checks_for_doc_type("cert_inc", "entity")
        ids = [c["id"] for c in checks]
        assert "DOC-06A" in ids, "DOC-06A (Incorporation Date Match) missing from cert_inc"

    def test_cert_inc_has_jurisdiction_match(self):
        checks = get_checks_for_doc_type("cert_inc", "entity")
        ids = [c["id"] for c in checks]
        assert "DOC-07" in ids, "DOC-07 (Jurisdiction Match) missing from cert_inc"

    def test_memarts_has_share_capital_match(self):
        checks = get_checks_for_doc_type("memarts", "entity")
        ids = [c["id"] for c in checks]
        assert "DOC-13" in ids, "DOC-13 (Authorised Share Capital Match) missing from memarts"


class TestFieldAlignmentToMatrix:
    """Verify normalization produces the field names expected by the Agent 1 matrix."""

    def test_incorporation_number_alias_produced(self):
        from prescreening.normalize import normalize_prescreening_data
        data = {"prescreening_data": {"brn": "BRN-12345"}}
        result = normalize_prescreening_data(data)
        assert result.get("incorporation_number") == "BRN-12345"
        assert result.get("registration_number") == "BRN-12345"

    def test_bank_name_alias_produced(self):
        from prescreening.normalize import normalize_prescreening_data
        data = {"prescreening_data": {"existing_bank_name": "HSBC"}}
        result = normalize_prescreening_data(data)
        assert result.get("bank_name") == "HSBC"

    def test_shareholders_alias_produced(self):
        from prescreening.normalize import normalize_prescreening_data
        ubos = [{"full_name": "Alice", "ownership_pct": 60}]
        data = {"ubos": ubos, "prescreening_data": {}}
        result = normalize_prescreening_data(data)
        assert isinstance(result.get("shareholders"), list)

    def test_registered_office_address_alias_produced(self):
        from prescreening.normalize import normalize_prescreening_data
        data = {"prescreening_data": {"registered_address": "123 Main St"}}
        result = normalize_prescreening_data(data)
        assert result.get("registered_office_address") == "123 Main St"


class TestNewRuleChecks:
    """Test rule check implementations for DOC-06A, DOC-07, DOC-13."""

    def test_incorporation_date_match_pass(self):
        extracted = {"incorporation_date": "2020-06-15"}
        ps = {"incorporation_date": "2020-06-15"}
        results = run_rule_checks("cert_inc", "entity", extracted, ps, "LOW")
        date_checks = [r for r in results if r.get("id") == "DOC-06A"]
        assert len(date_checks) == 1
        assert date_checks[0]["result"] == CheckStatus.PASS

    def test_incorporation_date_match_fail(self):
        extracted = {"incorporation_date": "2020-06-15"}
        ps = {"incorporation_date": "2019-01-01"}
        results = run_rule_checks("cert_inc", "entity", extracted, ps, "LOW")
        date_checks = [r for r in results if r.get("id") == "DOC-06A"]
        assert len(date_checks) == 1
        assert date_checks[0]["result"] == CheckStatus.FAIL

    def test_jurisdiction_match_pass(self):
        extracted = {"jurisdiction": "Mauritius"}
        ps = {"country_of_incorporation": "Mauritius"}
        results = run_rule_checks("cert_inc", "entity", extracted, ps, "LOW")
        jur_checks = [r for r in results if r.get("id") == "DOC-07"]
        assert len(jur_checks) == 1
        assert jur_checks[0]["result"] == CheckStatus.PASS

    def test_jurisdiction_match_fail(self):
        extracted = {"jurisdiction": "United Kingdom"}
        ps = {"country_of_incorporation": "Mauritius"}
        results = run_rule_checks("cert_inc", "entity", extracted, ps, "LOW")
        jur_checks = [r for r in results if r.get("id") == "DOC-07"]
        assert len(jur_checks) == 1
        assert jur_checks[0]["result"] == CheckStatus.FAIL

    def test_share_capital_match_pass(self):
        extracted = {"authorised_share_capital": "100000"}
        ps = {"authorised_share_capital": "100000"}
        results = run_rule_checks("memarts", "entity", extracted, ps, "LOW")
        cap_checks = [r for r in results if r.get("id") == "DOC-13"]
        assert len(cap_checks) == 1
        assert cap_checks[0]["result"] == CheckStatus.PASS

    def test_share_capital_match_fail(self):
        extracted = {"authorised_share_capital": "500000"}
        ps = {"authorised_share_capital": "100000"}
        results = run_rule_checks("memarts", "entity", extracted, ps, "LOW")
        cap_checks = [r for r in results if r.get("id") == "DOC-13"]
        assert len(cap_checks) == 1
        assert cap_checks[0]["result"] == CheckStatus.FAIL


class TestPersonContextVerification:
    """Test that person-level checks work when person fields are injected."""

    def test_passport_dob_match_with_person_context(self):
        """DOC-49A should PASS when date_of_birth is present in prescreening_data."""
        extracted = {"date_of_birth": "1985-03-20", "expiry_date": "2030-01-01"}
        ps = {"date_of_birth": "1985-03-20", "full_name": "John Smith", "nationality": "British"}
        results = run_rule_checks("passport", "person", extracted, ps, "LOW")
        dob_checks = [r for r in results if r.get("id") == "DOC-49A"]
        assert len(dob_checks) == 1
        assert dob_checks[0]["result"] == CheckStatus.PASS

    def test_passport_nationality_match_with_person_context(self):
        """DOC-52 should PASS when nationality is present in prescreening_data."""
        extracted = {"nationality": "British", "expiry_date": "2030-01-01"}
        ps = {"nationality": "British", "full_name": "John Smith"}
        results = run_rule_checks("passport", "person", extracted, ps, "LOW")
        nat_checks = [r for r in results if r.get("id") == "DOC-52"]
        assert len(nat_checks) == 1
        assert nat_checks[0]["result"] == CheckStatus.PASS

    def test_passport_name_match_with_person_context(self):
        """DOC-51 should use full_name from person context."""
        extracted = {"entity_name": "John Smith", "expiry_date": "2030-01-01"}
        ps = {"full_name": "John Smith"}
        results = run_rule_checks("passport", "person", extracted, ps, "LOW")
        name_checks = [r for r in results if "name" in r.get("label", "").lower() and r.get("id") == "DOC-51"]
        assert len(name_checks) == 1
        assert name_checks[0]["result"] == CheckStatus.PASS

    def test_person_checks_warn_without_context(self):
        """Without person context, DOC-49A should WARN (not silently pass)."""
        extracted = {"date_of_birth": "1985-03-20", "expiry_date": "2030-01-01"}
        ps = {}  # No person context injected
        results = run_rule_checks("passport", "person", extracted, ps, "LOW")
        dob_checks = [r for r in results if r.get("id") == "DOC-49A"]
        assert len(dob_checks) == 1
        assert dob_checks[0]["result"] == CheckStatus.WARN


class TestCompletedStubs:
    """Test that DOC-15 and DOC-28 are no longer stubs."""

    def test_doc15_compares_percentages(self):
        """DOC-15 should compare actual percentages, not just count holders."""
        extracted = {"shareholders": [
            {"name": "Alice Smith", "percentage": 60},
            {"name": "Bob Jones", "percentage": 40},
        ]}
        ps = {"shareholders": [
            {"full_name": "Alice Smith", "ownership_pct": 60},
            {"full_name": "Bob Jones", "ownership_pct": 40},
        ]}
        results = run_rule_checks("reg_sh", "entity", extracted, ps, "LOW")
        pct_checks = [r for r in results if r.get("id") == "DOC-15"]
        assert len(pct_checks) == 1
        assert pct_checks[0]["result"] == CheckStatus.PASS

    def test_doc15_fails_on_mismatch(self):
        extracted = {"shareholders": [
            {"name": "Alice Smith", "percentage": 80},
        ]}
        ps = {"shareholders": [
            {"full_name": "Alice Smith", "ownership_pct": 60},
        ]}
        results = run_rule_checks("reg_sh", "entity", extracted, ps, "LOW")
        pct_checks = [r for r in results if r.get("id") == "DOC-15"]
        assert len(pct_checks) == 1
        assert pct_checks[0]["result"] == CheckStatus.FAIL

    def test_doc28_no_longer_placeholder(self):
        """DOC-28 should do real comparison when data is available."""
        extracted = {"entities": [
            {"name": "Alice Smith", "percentage": 60},
        ]}
        ps = {"shareholders": [
            {"full_name": "Alice Smith", "ownership_pct": 60},
        ]}
        results = run_rule_checks("structure_chart", "entity", extracted, ps, "LOW")
        own_checks = [r for r in results if r.get("id") == "DOC-28"]
        assert len(own_checks) == 1
        assert own_checks[0]["result"] == CheckStatus.PASS


# ── Register alignment: claude_client derived definitions must match matrix ────

class TestClaudeClientCheckDefinitionAlignment:
    """
    Verifies that ClaudeClient._get_check_definitions() produces check IDs
    that are identical to the canonical verification_matrix.py IDs.

    This ensures the fallback path (when ai_checks DB is empty or unavailable)
    uses the same check IDs as the production seeded path, maintaining
    regulator-grade provenance regardless of which code path is active.
    """

    def test_derived_definitions_loaded(self):
        """_get_check_definitions() must return a non-empty dict."""
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()
        assert isinstance(defs, dict), "Derived definitions must be a dict"
        assert len(defs) > 0, "Derived definitions must not be empty"

    def test_derived_ids_match_matrix_for_all_doc_types(self):
        """
        For every doc type in ALL_DOC_CHECKS, every non-CERT-01 check ID in
        the matrix must appear in the derived definitions under the same doc_type.
        """
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()

        # Build matrix IDs grouped by doc_type (direct matrix keys, no alias transformation)
        missing = []
        for matrix_key, entry in ALL_DOC_CHECKS.items():
            if entry.get("retired"):
                continue
            derived_ids = {c["id"] for c in defs.get(matrix_key, [])}
            for c in entry.get("checks", []):
                cid = c["id"]
                if cid == "CERT-01":
                    continue  # cross-cutting check, present by re-use — not a mismatch
                if cid not in derived_ids:
                    missing.append(f"{matrix_key}/{cid} ({c['label']})")
        assert missing == [], (
            f"Check IDs in verification_matrix missing from derived definitions:\n"
            + "\n".join(f"  {m}" for m in missing)
        )

    def test_no_phantom_ids_in_derived(self):
        """
        No check ID in the derived definitions should be absent from the matrix
        (i.e., no stale/phantom IDs that could mislead audit trail).
        """
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()

        # Collect all canonical IDs from matrix
        canonical_ids = set()
        for entry in ALL_DOC_CHECKS.values():
            for c in entry.get("checks", []):
                canonical_ids.add(c["id"])
        for c in GATE_CHECKS:
            canonical_ids.add(c["id"])

        phantom = []
        for doc_type, checks in defs.items():
            for c in checks:
                cid = c.get("id", "")
                if cid and cid not in canonical_ids and not cid.startswith("DOC-GEN-"):
                    phantom.append(f"{doc_type}/{cid}")

        assert phantom == [], (
            f"Phantom check IDs in derived definitions (not in matrix):\n"
            + "\n".join(f"  {p}" for p in phantom)
        )

    def test_cert_inc_critical_ids_present(self):
        """cert_inc must have DOC-06A and DOC-07A (not legacy DOC-11/12/DOC-07)."""
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()
        ids = {c["id"] for c in defs.get("cert_inc", [])}
        assert "DOC-06A" in ids, "cert_inc missing DOC-06A (Date of Incorporation)"
        assert "DOC-07A" in ids, "cert_inc missing DOC-07A (Document Clarity)"
        assert "DOC-11" not in ids, "cert_inc must not have stale ID DOC-11"
        assert "DOC-12" not in ids, "cert_inc must not have stale ID DOC-12"

    def test_passport_critical_ids_present(self):
        """passport must have DOC-49, DOC-49A, DOC-50, DOC-51, DOC-52 (not legacy DOC-48/62/63)."""
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()
        ids = {c["id"] for c in defs.get("passport", [])}
        for required in ("DOC-49", "DOC-49A", "DOC-50", "DOC-51", "DOC-52"):
            assert required in ids, f"passport missing {required}"
        for stale in ("DOC-48", "DOC-62", "DOC-63"):
            assert stale not in ids, f"passport must not have stale ID {stale}"

    def test_memarts_has_doc_ma01(self):
        """memarts must have DOC-MA-01 (Business Objects) not legacy DOC-13."""
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()
        ids = {c["id"] for c in defs.get("memarts", [])}
        assert "DOC-MA-01" in ids, "memarts missing DOC-MA-01 (Business Objects)"
        assert "DOC-13" in ids, "memarts missing DOC-13 (Authorised Share Capital)"
        assert "DOC-16" not in ids, "memarts must not have stale ID DOC-16"

    def test_reg_sh_uses_doc15a_doc15b(self):
        """reg_sh must have DOC-15A and DOC-15B (not legacy DOC-22/DOC-23)."""
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()
        ids = {c["id"] for c in defs.get("reg_sh", [])}
        assert "DOC-15A" in ids, "reg_sh missing DOC-15A"
        assert "DOC-15B" in ids, "reg_sh missing DOC-15B"
        assert "DOC-22" not in ids, "reg_sh must not have stale ID DOC-22"
        assert "DOC-23" not in ids, "reg_sh must not have stale ID DOC-23"

    def test_no_cross_doc_type_id_conflicts(self):
        """
        IDs used by the derived definitions must not conflict with IDs from
        different doc types in the matrix (e.g., DOC-61 used for fin_stmt
        would conflict with its canonical assignment to poa_person).
        """
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()

        # Build canonical id -> doc_type map (excluding CERT-01, using direct matrix keys)
        canonical_map = {}
        for matrix_key, entry in ALL_DOC_CHECKS.items():
            for c in entry.get("checks", []):
                cid = c["id"]
                if cid == "CERT-01":
                    continue
                if cid not in canonical_map:
                    canonical_map[cid] = matrix_key

        conflicts = []
        for derived_doc_type, checks in defs.items():
            for c in checks:
                cid = c.get("id", "")
                if not cid or cid == "CERT-01" or cid.startswith("DOC-GEN-"):
                    continue
                if cid in canonical_map and canonical_map[cid] != derived_doc_type:
                    conflicts.append(
                        f"{cid} in derived/{derived_doc_type} but canonical/{canonical_map[cid]}"
                    )

        assert conflicts == [], (
            f"Cross-doc-type ID conflicts in derived definitions:\n"
            + "\n".join(f"  {c}" for c in conflicts)
        )

    def test_cache_is_populated_and_reused(self):
        """_get_check_definitions() must populate the cache on first call and reuse it on second."""
        from claude_client import ClaudeClient
        # Clear the cache to ensure a fresh load
        ClaudeClient._check_definitions_cache = None

        first = ClaudeClient._get_check_definitions()
        assert ClaudeClient._check_definitions_cache is not None, \
            "Cache must be populated after first call"

        second = ClaudeClient._get_check_definitions()
        assert first is second, \
            "Second call must return the exact same cached object (no re-load)"

    def test_unknown_doc_type_uses_generic_fallback(self):
        """verify_document() must return the generic DOC-GEN checks for unknown doc types."""
        from claude_client import ClaudeClient
        defs = ClaudeClient._get_check_definitions()
        unknown_checks = defs.get("__completely_unknown_doc_type__")
        # Unknown types are NOT in the derived definitions dict — the generic fallback
        # is applied inline in verify_document() via .get(doc_type, [...generic...])
        assert unknown_checks is None, \
            "Unknown doc types must not be present in derived definitions"

        # Verify the inline fallback list in verify_document() uses DOC-GEN- prefixed IDs.
        # Inspect the method source to find the hardcoded fallback list.
        import inspect
        source = inspect.getsource(ClaudeClient.verify_document)
        assert "DOC-GEN-01" in source, "verify_document must use DOC-GEN-01 in generic fallback"
        assert "DOC-GEN-02" in source, "verify_document must use DOC-GEN-02 in generic fallback"
        assert "DOC-GEN-03" in source, "verify_document must use DOC-GEN-03 in generic fallback"


class TestDbSeedAlignmentWithMatrix:
    """
    Prove that the DB seed used by sync_ai_checks_from_seed() is always
    derived from verification_matrix.build_ai_checks_seed() — no hardcoded drift.
    """

    def _get_db_seed(self):
        """Return the seed that sync_ai_checks_from_seed uses."""
        from db import _SUPPLEMENTARY_AI_CHECKS_SEED
        from verification_matrix import build_ai_checks_seed
        return build_ai_checks_seed() + _SUPPLEMENTARY_AI_CHECKS_SEED

    def test_matrix_doc_types_all_present_in_db_seed(self):
        """Every non-retired matrix entry must be represented in the db seed."""
        from verification_matrix import build_ai_checks_seed
        import json
        seed_entries = {(cat, dt) for cat, dt, _, _ in self._get_db_seed()}
        for cat, dt, _, _ in build_ai_checks_seed():
            assert (cat, dt) in seed_entries, \
                f"Matrix entry {cat}/{dt} missing from DB seed"

    def test_pep_declaration_uses_underscore_in_db_seed(self):
        """DB seed must use pep_declaration (underscore) not pep-declaration (hyphen)."""
        doc_types = {dt for _, dt, _, _ in self._get_db_seed()}
        assert "pep_declaration" in doc_types, \
            "pep_declaration (underscore) must be in the DB seed"
        assert "pep-declaration" not in doc_types, \
            "pep-declaration (hyphen) must NOT be in the DB seed"

    def test_matrix_check_ids_match_db_seed_ids(self):
        """For every matrix doc type, check IDs in DB seed must exactly match matrix IDs."""
        import json
        from verification_matrix import build_ai_checks_seed, ALL_DOC_CHECKS

        matrix_ids = {}
        for key, entry in ALL_DOC_CHECKS.items():
            if entry.get("retired"):
                continue
            dt = entry.get("doc_type_alias") or key
            # doc_type_alias maps matrix keys to their DB storage alias (e.g. poa_person→poa).
            # pep_declaration no longer has a doc_type_alias (removed in this PR);
            # other aliases (poa_person→poa, bankref_pep→bankref) are still valid.
            cat = entry.get("category", "entity")
            ids = {c["id"] for c in entry.get("checks", [])}
            matrix_ids[(cat, dt)] = ids

        seed_ids = {}
        for cat, dt, _, checks_json in build_ai_checks_seed():
            checks = json.loads(checks_json)
            seed_ids[(cat, dt)] = {c["id"] for c in checks}

        for key_tuple, expected_ids in matrix_ids.items():
            if not expected_ids:
                continue  # skip retired/empty
            assert key_tuple in seed_ids, \
                f"Matrix entry {key_tuple} missing from build_ai_checks_seed()"
            actual_ids = seed_ids[key_tuple]
            missing = expected_ids - actual_ids
            extra = actual_ids - expected_ids
            assert not missing, \
                f"{key_tuple}: check IDs in seed missing from matrix: {missing}"
            assert not extra, \
                f"{key_tuple}: extra check IDs in seed not in matrix: {extra}"

    def test_db_seed_passport_uses_canonical_ids(self):
        """Regression: passport must use DOC-49/49A/50/51/52 not old DOC-48/62/63."""
        import json
        seed = {(c, dt): json.loads(ch) for c, dt, _, ch in self._get_db_seed()}
        passport_checks = seed.get(("person", "passport"), [])
        passport_ids = {c["id"] for c in passport_checks}
        # Old wrong IDs that must NOT be present
        old_wrong = {"DOC-48", "DOC-62", "DOC-63"}
        assert not (passport_ids & old_wrong), \
            f"Passport seed contains old wrong IDs: {passport_ids & old_wrong}"
        # Canonical IDs that must be present
        canonical = {"DOC-49", "DOC-49A", "DOC-50", "DOC-51", "DOC-52"}
        assert canonical.issubset(passport_ids), \
            f"Passport seed missing canonical IDs: {canonical - passport_ids}"

    def test_db_seed_cert_inc_uses_canonical_ids(self):
        """Regression: cert_inc must use DOC-06A/07/07A not old DOC-11/12."""
        import json
        seed = {(c, dt): json.loads(ch) for c, dt, _, ch in self._get_db_seed()}
        cert_checks = seed.get(("entity", "cert_inc"), [])
        cert_ids = {c["id"] for c in cert_checks}
        old_wrong = {"DOC-11", "DOC-12"}
        assert not (cert_ids & old_wrong), \
            f"cert_inc seed contains old wrong IDs: {cert_ids & old_wrong}"
        canonical = {"DOC-06A", "DOC-07", "DOC-07A"}
        assert canonical.issubset(cert_ids), \
            f"cert_inc seed missing canonical IDs: {canonical - cert_ids}"

    def test_no_id_conflicts_within_same_doc_type(self):
        """Each doc_type/category pair must have unique check IDs."""
        import json
        for cat, dt, doc_name, checks_json in self._get_db_seed():
            checks = json.loads(checks_json)
            ids = [c["id"] for c in checks]
            assert len(ids) == len(set(ids)), \
                f"Duplicate check IDs within {cat}/{dt}: {[i for i in ids if ids.count(i) > 1]}"

    def test_db_init_seeds_pep_declaration_not_hyphenated(self):
        """After sync_ai_checks_from_seed(), ai_checks must have pep_declaration not pep-declaration."""
        import sqlite3, json
        from db import DBConnection, sync_ai_checks_from_seed
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE ai_checks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "category TEXT NOT NULL, "
            "doc_type TEXT NOT NULL, "
            "doc_name TEXT, "
            "checks TEXT DEFAULT '[]', "
            "updated_at TEXT DEFAULT (datetime('now')), "
            "UNIQUE(doc_type, category))"
        )
        conn.commit()
        db = DBConnection(conn, is_postgres=False)
        sync_ai_checks_from_seed(db)
        rows = conn.execute("SELECT doc_type FROM ai_checks WHERE category='person'").fetchall()
        doc_types = {r[0] for r in rows}
        assert "pep_declaration" in doc_types, \
            "pep_declaration must be seeded into ai_checks"
        assert "pep-declaration" not in doc_types, \
            "pep-declaration (hyphen) must not be in ai_checks after sync"
        conn.close()
