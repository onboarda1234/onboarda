"""
Tests for protected_controls.py — EX-01 through EX-13 registry.
"""

import os
import pytest

from protected_controls import (
    PROTECTED_CONTROLS,
    PROTECTED_FILES,
    PROTECTED_HTML_FILES,
    verify_control_coverage,
    check_protected_files_in_diff,
)


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestProtectedControlsRegistry:
    """All 13 EX controls must be registered with valid metadata."""

    def test_all_13_controls_registered(self):
        for i in range(1, 14):
            cid = f"EX-{str(i).zfill(2)}"
            assert cid in PROTECTED_CONTROLS, f"{cid} not registered"

    def test_each_control_has_description(self):
        for cid, ctrl in PROTECTED_CONTROLS.items():
            assert ctrl.get("description"), f"{cid} missing description"

    def test_each_control_has_critical_files(self):
        for cid, ctrl in PROTECTED_CONTROLS.items():
            assert ctrl.get("critical_files"), f"{cid} missing critical_files"

    def test_each_control_has_test_files(self):
        for cid, ctrl in PROTECTED_CONTROLS.items():
            assert ctrl.get("test_files"), f"{cid} missing test_files"

    def test_each_control_has_gate_ids_key(self):
        for cid, ctrl in PROTECTED_CONTROLS.items():
            assert "gate_ids" in ctrl, f"{cid} missing gate_ids key"


class TestControlCoverage:
    """verify_control_coverage() must return empty list when all files exist."""

    def test_verify_control_coverage_returns_empty(self):
        errors = verify_control_coverage()
        assert errors == [], f"Coverage errors: {errors}"

    def test_all_critical_files_exist(self):
        for cid, ctrl in PROTECTED_CONTROLS.items():
            for cf in ctrl["critical_files"]:
                fpath = os.path.join(BACKEND_DIR, cf)
                assert os.path.exists(fpath), f"{cid}: critical file '{cf}' not found at {fpath}"

    def test_all_test_files_exist(self):
        for cid, ctrl in PROTECTED_CONTROLS.items():
            for tf in ctrl["test_files"]:
                fpath = os.path.join(BACKEND_DIR, tf)
                assert os.path.exists(fpath), f"{cid}: test file '{tf}' not found at {fpath}"


class TestProtectedFilesList:
    """PROTECTED_FILES list must contain all known protected files."""

    def test_minimum_protected_files_count(self):
        assert len(PROTECTED_FILES) >= 17

    def test_all_protected_files_exist(self):
        for pf in PROTECTED_FILES:
            fpath = os.path.join(BACKEND_DIR, pf)
            assert os.path.exists(fpath), f"Protected file '{pf}' not found"

    def test_protected_html_files_exist(self):
        repo_root = os.path.dirname(BACKEND_DIR)
        for hf in PROTECTED_HTML_FILES:
            fpath = os.path.join(repo_root, hf)
            assert os.path.exists(fpath), f"Protected HTML file '{hf}' not found"


class TestProtectedFileDiffGuard:
    """check_protected_files_in_diff() must detect protected file modifications."""

    def test_no_violations_for_new_files(self):
        changed = [
            "arie-backend/screening_config.py",
            "arie-backend/screening_models.py",
            "arie-backend/tests/test_screening_models.py",
        ]
        violations = check_protected_files_in_diff(changed)
        assert violations == []

    def test_detects_protected_file_modification(self):
        changed = [
            "arie-backend/screening_config.py",
            "arie-backend/screening.py",
        ]
        violations = check_protected_files_in_diff(changed)
        assert "arie-backend/screening.py" in violations

    def test_detects_multiple_violations(self):
        changed = [
            "arie-backend/memo_handler.py",
            "arie-backend/rule_engine.py",
            "arie-backend/new_file.py",
        ]
        violations = check_protected_files_in_diff(changed)
        assert len(violations) == 2

    def test_detects_html_file_modification(self):
        changed = ["arie-backoffice.html"]
        violations = check_protected_files_in_diff(changed)
        assert len(violations) == 1

    def test_detects_db_py_modification(self):
        changed = ["arie-backend/db.py"]
        violations = check_protected_files_in_diff(changed)
        assert "arie-backend/db.py" in violations

    def test_empty_diff_returns_no_violations(self):
        assert check_protected_files_in_diff([]) == []

    def test_detects_all_protected_files(self):
        all_changed = [f"arie-backend/{f}" for f in PROTECTED_FILES]
        violations = check_protected_files_in_diff(all_changed)
        assert len(violations) == len(PROTECTED_FILES)
