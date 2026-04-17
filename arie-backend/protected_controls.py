"""
Protected Controls Register — ComplyAdvantage Migration Safety Layer
====================================================================
Catalogues all EX-01 through EX-13 audit-validated controls so that
migration work can verify nothing regresses.

This module is **additive only**. It does NOT modify any existing test,
module, or workflow.  It provides a read-only registry and a helper
that checks listed test files still exist on disk.
"""

import os
import logging

logger = logging.getLogger("arie")

# ---------------------------------------------------------------------------
# Registry: each key is the control ID; value describes what it protects.
# ---------------------------------------------------------------------------
PROTECTED_CONTROLS = {
    "EX-01": {
        "description": "Sumsub webhook idempotency and DLQ routing",
        "critical_files": [
            "server.py",
            "screening.py",
        ],
        "test_files": [
            "tests/test_screening_blockers.py",
            "tests/test_sumsub_hardening_pr14.py",
        ],
        "gate_ids": [],
    },
    "EX-02": {
        "description": "Screening error enrichment and structured error metadata",
        "critical_files": [
            "sumsub_client.py",
        ],
        "test_files": [
            "tests/test_sumsub_error_enrichment.py",
        ],
        "gate_ids": [],
    },
    "EX-03": {
        "description": "Screening mode detection and simulated-screening blocking",
        "critical_files": [
            "security_hardening.py",
        ],
        "test_files": [
            "tests/test_screening_mode.py",
        ],
        "gate_ids": ["gate_2b", "gate_5"],
    },
    "EX-04": {
        "description": "Webhook handler base-class alignment and error method",
        "critical_files": [
            "server.py",
            "screening.py",
        ],
        "test_files": [
            "tests/test_screening_blockers.py",
            "tests/test_ex01_ex04_closure.py",
        ],
        "gate_ids": [],
    },
    "EX-05": {
        "description": "Floor-rule validation and risk-config integrity",
        "critical_files": [
            "rule_engine.py",
        ],
        "test_files": [
            "tests/test_floor_rule_validation.py",
            "tests/test_risk_config_integrity.py",
            "tests/test_risk_config_shape.py",
        ],
        "gate_ids": [],
    },
    "EX-06": {
        "description": "Memo staleness gate using inputs_updated_at",
        "critical_files": [
            "security_hardening.py",
            "server.py",
            "db.py",
        ],
        "test_files": [
            "tests/test_memo_staleness_approval.py",
            "tests/test_memo_ordering_gate.py",
        ],
        "gate_ids": ["gate_staleness"],
    },
    "EX-07": {
        "description": "Approval gate hardening and prescreening validation",
        "critical_files": [
            "security_hardening.py",
        ],
        "test_files": [
            "tests/test_approval_gate.py",
            "tests/test_prescreening_fixes.py",
        ],
        "gate_ids": ["gate_2", "gate_2b", "gate_5"],
    },
    "EX-08": {
        "description": "Sumsub applicant ID validation and country code fix",
        "critical_files": [
            "screening.py",
            "sumsub_client.py",
        ],
        "test_files": [
            "tests/test_ex08_applicant_id_validation.py",
            "tests/test_create_applicant_country_fix.py",
        ],
        "gate_ids": [],
    },
    "EX-09": {
        "description": "Risk config version capture and recomputation on screening re-run",
        "critical_files": [
            "rule_engine.py",
            "server.py",
        ],
        "test_files": [
            "tests/test_risk_recomputation.py",
            "tests/test_risk_config_integrity.py",
        ],
        "gate_ids": [],
    },
    "EX-10": {
        "description": "Screening freshness validation — Gate 9 blocks stale results",
        "critical_files": [
            "security_hardening.py",
            "environment.py",
            "server.py",
        ],
        "test_files": [
            "tests/test_screening_freshness.py",
        ],
        "gate_ids": ["gate_9"],
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
        "description": "Client-side security hardening — role guards, logout cleanup",
        "critical_files": [
            "server.py",
        ],
        "test_files": [
            "tests/test_ex12_client_security.py",
        ],
        "gate_ids": [],
    },
    "EX-13": {
        "description": "Batch-fetch N+1 elimination and ETag conditional refresh",
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


def verify_control_coverage(base_dir: str = None) -> list:
    """
    Check that every test file referenced by PROTECTED_CONTROLS exists on disk.

    Returns a list of error strings.  An empty list means full coverage.

    Parameters
    ----------
    base_dir : str, optional
        Root of the arie-backend tree.  Defaults to the directory containing
        this module.
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    errors = []
    for control_id, spec in PROTECTED_CONTROLS.items():
        for tf in spec.get("test_files", []):
            full_path = os.path.join(base_dir, tf)
            if not os.path.isfile(full_path):
                errors.append(
                    f"{control_id}: test file '{tf}' not found at {full_path}"
                )
    return errors


def get_control(control_id: str) -> dict:
    """Return the spec for a single control, or empty dict if unknown."""
    return PROTECTED_CONTROLS.get(control_id, {})


def list_control_ids() -> list:
    """Return sorted list of all registered control IDs."""
    return sorted(PROTECTED_CONTROLS.keys())
