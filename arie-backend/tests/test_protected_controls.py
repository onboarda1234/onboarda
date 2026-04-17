"""
Tests for protected_controls.py — SCR-001
==========================================
Validates the EX-01 → EX-13 control registry and coverage verification.
"""

import os
import pytest
from protected_controls import (
    PROTECTED_CONTROLS,
    verify_control_coverage,
    get_control,
    list_control_ids,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestProtectedControlsRegistry:
    """All 13 EX controls must be registered with required fields."""

    def test_all_13_controls_registered(self):
        expected = {f"EX-{str(i).zfill(2)}" for i in range(1, 14)}
        assert expected == set(PROTECTED_CONTROLS.keys())

    def test_each_control_has_required_fields(self):
        for cid, spec in PROTECTED_CONTROLS.items():
            assert "description" in spec, f"{cid} missing description"
            assert "critical_files" in spec, f"{cid} missing critical_files"
            assert "test_files" in spec, f"{cid} missing test_files"
            assert "gate_ids" in spec, f"{cid} missing gate_ids"

    def test_descriptions_are_non_empty_strings(self):
        for cid, spec in PROTECTED_CONTROLS.items():
            assert isinstance(spec["description"], str), f"{cid} description not str"
            assert len(spec["description"]) > 5, f"{cid} description too short"

    def test_critical_files_are_lists(self):
        for cid, spec in PROTECTED_CONTROLS.items():
            assert isinstance(spec["critical_files"], list), f"{cid} critical_files not list"
            assert len(spec["critical_files"]) > 0, f"{cid} critical_files empty"

    def test_test_files_are_lists(self):
        for cid, spec in PROTECTED_CONTROLS.items():
            assert isinstance(spec["test_files"], list), f"{cid} test_files not list"
            assert len(spec["test_files"]) > 0, f"{cid} test_files empty"

    def test_gate_ids_are_lists(self):
        for cid, spec in PROTECTED_CONTROLS.items():
            assert isinstance(spec["gate_ids"], list), f"{cid} gate_ids not list"


class TestControlCoverageVerification:
    """verify_control_coverage() checks all referenced test files exist."""

    def test_coverage_passes_against_real_repo(self):
        errors = verify_control_coverage(BASE_DIR)
        assert errors == [], f"Coverage gaps: {errors}"

    def test_coverage_detects_missing_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # No test files in tmpdir → every reference should fail
            errors = verify_control_coverage(tmpdir)
            assert len(errors) > 0


class TestHelperFunctions:
    """get_control() and list_control_ids() work correctly."""

    def test_get_existing_control(self):
        spec = get_control("EX-01")
        assert "description" in spec

    def test_get_unknown_control(self):
        assert get_control("EX-99") == {}

    def test_list_control_ids_sorted(self):
        ids = list_control_ids()
        assert ids == sorted(ids)
        assert len(ids) == 13

    def test_list_control_ids_contains_all(self):
        ids = list_control_ids()
        for i in range(1, 14):
            assert f"EX-{str(i).zfill(2)}" in ids
