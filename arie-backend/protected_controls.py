"""
Protected Controls Register — EX-01 through EX-13
==================================================
Registry of all audit-validated controls from the EX remediation sprint.
Used for CI enforcement and coverage verification.

This module is additive-only and must not modify any protected file.
"""

import os

# ── EX-01 through EX-13 Protected Controls ──

PROTECTED_CONTROLS = {
    "EX-01": {
        "description": "Screening provider evidence collection and mode determination",
        "critical_files": [
            "security_hardening.py",
            "screening.py",
        ],
        "test_files": [
            "tests/test_screening_mode.py",
            "tests/test_security_hardening_extended.py",
            "tests/test_ex01_ex04_closure.py",
        ],
        "gate_ids": ["Gate 2", "Gate 2b", "Gate 5"],
    },
    "EX-02": {
        "description": "Sumsub webhook ingestion and idempotency",
        "critical_files": [
            "server.py",
            "sumsub_client.py",
        ],
        "test_files": [
            "tests/test_sumsub_integrity_hardening.py",
            "tests/test_sumsub_dual_sig.py",
        ],
        "gate_ids": [],
    },
    "EX-03": {
        "description": "Memo generation and validation pipeline",
        "critical_files": [
            "memo_handler.py",
            "validation_engine.py",
        ],
        "test_files": [
            "tests/test_validation_engine.py",
            "tests/test_ex01_ex04_closure.py",
        ],
        "gate_ids": ["Gate 8"],
    },
    "EX-04": {
        "description": "Supervisor contradiction detection",
        "critical_files": [
            "supervisor_engine.py",
        ],
        "test_files": [
            "tests/test_supervisor.py",
            "tests/test_ex01_ex04_closure.py",
        ],
        "gate_ids": [],
    },
    "EX-05": {
        "description": "Risk scoring and elevation",
        "critical_files": [
            "rule_engine.py",
            "security_hardening.py",
        ],
        "test_files": [
            "tests/test_rule_engine.py",
            "tests/test_risk_scoring.py",
            "tests/test_risk_elevation.py",
        ],
        "gate_ids": ["Gate 9"],
    },
    "EX-06": {
        "description": "PEP detection and deduplication",
        "critical_files": [
            "screening.py",
            "rule_engine.py",
        ],
        "test_files": [
            "tests/test_screening_unit.py",
        ],
        "gate_ids": [],
    },
    "EX-07": {
        "description": "Approval gate evaluation",
        "critical_files": [
            "security_hardening.py",
            "server.py",
        ],
        "test_files": [
            "tests/test_approval_gate.py",
            "tests/test_security_hardening_extended.py",
        ],
        "gate_ids": ["Gate 2", "Gate 2b", "Gate 5", "Gate 8", "Gate 9"],
    },
    "EX-08": {
        "description": "KYC applicant creation via Sumsub",
        "critical_files": [
            "sumsub_client.py",
            "screening.py",
        ],
        "test_files": [
            "tests/test_sumsub_verification.py",
            "tests/test_ex08_applicant_id_validation.py",
        ],
        "gate_ids": [],
    },
    "EX-09": {
        "description": "Risk config version tracking and recomputation",
        "critical_files": [
            "rule_engine.py",
            "server.py",
        ],
        "test_files": [
            "tests/test_risk_config_integrity.py",
            "tests/test_risk_recomputation.py",
        ],
        "gate_ids": [],
    },
    "EX-10": {
        "description": "Screening freshness validation",
        "critical_files": [
            "security_hardening.py",
            "environment.py",
        ],
        "test_files": [
            "tests/test_screening_freshness.py",
        ],
        "gate_ids": ["Gate 9"],
    },
    "EX-11": {
        "description": "Officer sign-off enforcement and AI advisory labeling",
        "critical_files": [
            "server.py",
        ],
        "test_files": [
            "tests/test_ex11_signoff_enforcement.py",
            "tests/test_ex11_ai_advisory_labels.py",
        ],
        "gate_ids": [],
    },
    "EX-12": {
        "description": "Client-side security hardening (role guards, logout cleanup)",
        "critical_files": [
            "server.py",
            "auth.py",
        ],
        "test_files": [
            "tests/test_ex12_client_security.py",
            "tests/test_auth.py",
        ],
        "gate_ids": [],
    },
    "EX-13": {
        "description": "Batch-fetch N+1 elimination and ETag caching",
        "critical_files": [
            "party_utils.py",
            "server.py",
        ],
        "test_files": [
            "tests/test_ex13_batch_refresh.py",
        ],
        "gate_ids": [],
    },
}

# ── All protected files across all controls ──

PROTECTED_FILES = [
    "memo_handler.py",
    "rule_engine.py",
    "validation_engine.py",
    "supervisor_engine.py",
    "security_hardening.py",
    "sumsub_client.py",
    "screening.py",
    "auth.py",
    "base_handler.py",
    "change_management.py",
    "gdpr.py",
    "party_utils.py",
    "production_controls.py",
    "pdf_generator.py",
    "claude_client.py",
    "document_verification.py",
    "db.py",
]

PROTECTED_HTML_FILES = [
    "arie-backoffice.html",
    "arie-portal.html",
]


def verify_control_coverage() -> list:
    """
    Verify all EX controls are registered and their referenced files exist.

    Returns a list of error messages. Empty list means all controls are covered.
    """
    errors = []
    backend_dir = os.path.dirname(os.path.abspath(__file__))

    for control_id in [f"EX-{str(i).zfill(2)}" for i in range(1, 14)]:
        if control_id not in PROTECTED_CONTROLS:
            errors.append(f"{control_id} is not registered in PROTECTED_CONTROLS")
            continue

        control = PROTECTED_CONTROLS[control_id]

        if not control.get("description"):
            errors.append(f"{control_id} has no description")

        for cf in control.get("critical_files", []):
            fpath = os.path.join(backend_dir, cf)
            if not os.path.exists(fpath):
                errors.append(f"{control_id}: critical file '{cf}' does not exist")

        for tf in control.get("test_files", []):
            fpath = os.path.join(backend_dir, tf)
            if not os.path.exists(fpath):
                errors.append(f"{control_id}: test file '{tf}' does not exist")

    return errors


def check_protected_files_in_diff(changed_files: list) -> list:
    """
    Check if any protected files appear in a list of changed file paths.

    Args:
        changed_files: List of file paths changed in a PR/commit.

    Returns:
        List of protected files that were modified.
    """
    violations = []
    all_protected = PROTECTED_FILES + PROTECTED_HTML_FILES

    for changed in changed_files:
        # Normalize: strip leading paths to get the filename
        basename = os.path.basename(changed)
        # Also check with arie-backend/ prefix
        for protected in all_protected:
            pbase = os.path.basename(protected)
            if basename == pbase:
                violations.append(changed)
                break

    return violations
