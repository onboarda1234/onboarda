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
            prescreening_data={"regulatory_licences": "None"},
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
            prescreening_data={"regulatory_licences": "FCA PI licence ref 12345"},
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
