#!/usr/bin/env python3
"""Generate deterministic draft/final Compliance Memo validation samples."""

from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "validation" / "compliance-memo"
sys.path.insert(0, str(BACKEND_ROOT))

from pdf_generator import generate_memo_pdf  # noqa: E402


def _sample_memo():
    return {
        "application_ref": "ARF-2026-VAL-001",
        "company_name": "Meridian Trade Technologies Ltd",
        "memo_generated": "2026-07-31T10:15:00+00:00",
        "sections": {
            "executive_summary": {
                "content": (
                    "Ownership is acceptable because the sole beneficial owner and controlling "
                    "director are consistently identified and verified, with no unexplained control "
                    "layer. Current screening records no confirmed sanctions, PEP or adverse-media "
                    "matches. Cross-border exposure and limited trading history retain a medium "
                    "residual risk, but evidenced activity and a credible funding profile keep that "
                    "risk within appetite. Requiring the bank reference before activation and "
                    "enhanced first-year monitoring makes conditional approval proportionate."
                )
            },
            "risk_assessment": {
                "content": (
                    "Residual risk is MEDIUM. The primary drivers are cross-border trade exposure "
                    "and a recently established operating history; transparent ownership, verified "
                    "corporate evidence and a credible funding profile reduce the assessed risk."
                )
            },
            "red_flags_and_mitigants": {
                "red_flags": [
                    "Cross-border supplier payments include two higher-risk trade corridors.",
                    "The operating company has fewer than three years of trading history.",
                ],
                "mitigants": [
                    "The 100% beneficial owner and controlling director are identified and verified.",
                    "No confirmed sanctions, PEP or adverse-media matches are recorded in the screening snapshot.",
                    "Expected payment activity is supported by contracts and management accounts.",
                ],
                "approval_blockers": [
                    "Obtain the outstanding certified bank reference before account activation.",
                    "Record acceptance of the enhanced first-year transaction monitoring profile.",
                ],
            },
            "compliance_decision": {
                "decision": "APPROVE_WITH_CONDITIONS",
                "content": (
                    "On the evidence available at the application snapshot, the appropriate recommendation "
                    "is to approve Meridian Trade Technologies Ltd subject to conditions. The ownership "
                    "structure is transparent: the sole beneficial owner and controlling director are "
                    "identified and verified, and the declared activity is consistent with the corporate "
                    "evidence and expected payment profile. Current screening records no confirmed "
                    "sanctions, PEP or adverse-media matches. Residual exposure arises from higher-risk "
                    "cross-border trade corridors and the company's limited operating history. Those "
                    "factors warrant control but are moderated by verified ownership, a credible funding "
                    "profile and documented commercial purpose. Receipt of the outstanding bank reference "
                    "before activation, enhanced first-year monitoring and an early transaction review "
                    "address the identified exposure proportionately. Approval with conditions therefore "
                    "reflects the evidence, maintains appropriate control at activation and supports "
                    "effective ongoing oversight."
                ),
            },
            "ongoing_monitoring": {
                "content": (
                    "Apply the medium-risk monitoring profile, review cross-border transaction "
                    "patterns after 90 days, and complete the first periodic KYC review within 12 months."
                )
            },
            "audit_and_governance": {
                "content": "Authoritative evidence and workflow events remain in the application evidence pack."
            },
        },
        "metadata": {
            "memo_version": "v2",
            "memo_generated_at": "2026-07-31T10:15:00+00:00",
            "application_snapshot_timestamp": "2026-07-31T10:12:34+00:00",
            "model_version": "risk-model-v1.1",
            "risk_rating": "MEDIUM",
            "risk_score": 47,
            "risk_calculated_at": "2026-07-31T10:11:58+00:00",
            "approval_recommendation": "APPROVE_WITH_CONDITIONS",
            "mandatory_conditions": [
                "Obtain the outstanding certified bank reference before account activation."
            ],
            "operational_conditions": [
                "Record acceptance of the enhanced first-year transaction monitoring profile."
            ],
            "memo_input_hash": "c40ff2ae5f193d6f51d0872c8b46bb529bc80745d30370fe1bb6607869eb9b02",
            "rule_engine": {"engine_status": "CLEAN"},
            "build": {"git_sha_short": "validation"},
            "canonical_screening_current_summary": {
                "canonical_state": "clear_no_confirmed_matches",
                "provider_mode": "live",
                "snapshot_at": "2026-07-31T10:10:42+00:00",
            },
        },
    }


def _sample_application():
    return {
        "id": "app-validation-001",
        "ref": "ARF-2026-VAL-001",
        "company_name": "Meridian Trade Technologies Ltd",
        "risk_level": "MEDIUM",
        "risk_score": 47,
        "inputs_updated_at": "2026-07-31T10:12:34+00:00",
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    memo = _sample_memo()
    application = _sample_application()
    validation = {"validation_status": "pass", "quality_score": 9.2}

    draft = generate_memo_pdf(
        memo_data=memo,
        application=application,
        validation_result=validation,
    )
    final = generate_memo_pdf(
        memo_data=memo,
        application=application,
        validation_result=validation,
        approved_by="Senior Compliance Officer",
        approved_at="2026-07-31T11:02:17+00:00",
        approval_reason=(
            "I have reviewed the ownership evidence, current screening snapshot, residual risk position "
            "and proposed controls. The recommendation is proportionate to the identified exposure. "
            "Approval is granted subject to receipt of the certified bank reference before activation "
            "and implementation of the enhanced first-year monitoring profile."
        ),
    )

    draft_path = OUTPUT_DIR / "compliance-memo-draft-sample.pdf"
    final_path = OUTPUT_DIR / "compliance-memo-final-sample.pdf"
    draft_path.write_bytes(draft)
    final_path.write_bytes(final)
    print(f"{draft_path} ({len(draft)} bytes)")
    print(f"{final_path} ({len(final)} bytes)")


if __name__ == "__main__":
    main()
