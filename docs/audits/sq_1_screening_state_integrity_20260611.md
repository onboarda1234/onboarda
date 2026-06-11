# SQ-1 Screening Queue State Integrity

Date: 2026-06-11  
Branch: `codex/sq-1-screening-state-integrity`  
Repository: `onboarda1234/onboarda`  

## Verdict

Final verdict: **PASS WITH MINOR ISSUES**

The Screening Queue now fails closed on contradictory provider/review state at read time. Rows cannot be emitted as canonical `Clear` unless the provider result is terminal and either has no hits or has terminal hits explicitly cleared by an officer. Non-terminal, failed, missing, or contradictory provider states are normalized to `Not Started`, `Screening In Progress`, `Review Required`, `Escalated`, or `Failed / Provider Error`.

Minor issue: staging browser validation was not completed in this branch because the branch has not been deployed to staging. The unauthenticated staging `/api/version` request returned `401`, so deployed SHA could not be confirmed without an approved staging token in this thread.

## Provider Classification

SQ-1 queue state is AML / PEP / sanctions screening state, not identity verification state.

- Identity Verification: Sumsub, including ID Verification, Liveness & Face Match, Email/Phone Verification, Sumsub ID, and Reusable KYC.
- AML / PEP / Sanctions Screening: ComplyAdvantage / Accuity / other configured screening provider.

Sumsub must not be treated as the primary AML/PEP/sanctions screening provider unless a separate AML Screening entitlement and API response evidence are confirmed. This implementation therefore labels queue provider fields as screening-provider status and keeps Sumsub identity verification terminology out of the Screening Queue state model.

## Source Of Truth

- `origin/main` SHA at branch creation: `2b2f51b02a4968c5e4b85a13dcaccbdb353c2afd`
- Branch base SHA: `2b2f51b02a4968c5e4b85a13dcaccbdb353c2afd`
- Local HEAD before implementation: `2b2f51b02a4968c5e4b85a13dcaccbdb353c2afd`
- Staging `/api/version`: unauthenticated request returned `401`; pending post-deploy authenticated validation.

## Canonical Business Model

The queue now exposes one canonical officer-facing business state:

- `Not Started`
- `Screening In Progress`
- `Clear`
- `Review Required`
- `Escalated`
- `Failed / Provider Error`

Legacy/status-specific values such as `cleared_by_officer` and `follow_up_required` remain available in `status_key` for compatibility, but `canonical_status` is collapsed to the six-state business model. `cleared_by_officer` maps to canonical `Clear` only when terminal provider evidence exists.

## API Contract

`/api/screening/queue` rows now include:

- `canonical_status`
- `canonical_status_key`
- `officer_label`
- `provider_status`
- `screening_provider_status`
- `provider_status_scope=aml_pep_sanctions_screening`
- `terminal`
- `is_terminal`
- `total_hits`
- `has_hits`
- `officer_review_status`
- `review_evidence_present`
- `defensible_clear`
- `requires_review`
- `state_integrity_flags`
- `blocking_flags`
- `reasons`
- `raw_status`

`state_integrity_flags` records suppressed contradictions such as:

- `terminal_true_with_non_terminal_provider_status`
- `non_terminal_claimed_clear`
- `unreviewed_hits_claimed_clear`
- `unreviewed_hits_claimed_defensible_clear`
- `officer_clear_with_non_terminal_provider`
- `officer_clear_without_terminal_provider`
- `provider_error_claimed_clear`
- `legacy_normalized_state_conflict`

## Before / After Examples

| Case | Before | After |
| --- | --- | --- |
| `status_key=screening_pending`, `terminal=true`, no hits | Could resolve to `Clear` | `Screening In Progress`, `terminal=false`, `defensible_clear=false`, integrity flag |
| `total_hits > 0`, `screening_result=clear`, no officer review | Could carry clear/defensible-clear signal | `Review Required`, `defensible_clear=false`, integrity flags |
| `total_hits > 0`, officer cleared, provider pending | Could be treated as officer-cleared | `Review Required`, no defensible clear, integrity flag |
| `total_hits > 0`, officer cleared, provider terminal match | `cleared_by_officer` | canonical `Clear`, `officer_review_status=cleared`, `defensible_clear=true` |
| provider failed/not configured with clear claim | Risk of clear leakage | `Failed / Provider Error`, `defensible_clear=false` |

## Files Changed

- `arie-backend/screening_state.py`: added queue business-state mapping, screening-provider terminality guards, integrity flags, screening-provider/officer status fields, and fail-closed resolution order.
- `arie-backend/server.py`: queue filtering now uses canonical business status first.
- `arie-backoffice.html`: Screening Queue and triage badges render canonical business state; context shows screening-provider status and integrity flag marker.
- `arie-backend/tests/test_screening_queue_state_integrity.py`: added regressions for pending+terminal, hits+defensible-clear, officer-clear without terminal evidence, and queue API field shape.
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`: static assertion updated to enforce canonical queue status rendering.
- `arie-backend/migrations/scripts/migration_033_screening_queue_state_integrity.sql`: safe audit table for future controlled backfill findings; does not mutate application data.

## Migration / Backfill Safety

No existing application screening records are rewritten. Legacy contradictory data is normalized at queue read time. The migration adds `screening_state_integrity_backfill_log` as an audit target for a future controlled data-repair sprint, if needed.

## Validation

Commands run locally:

- `python3 -m py_compile server.py screening.py screening_normalizer.py screening_state.py rule_engine.py base_handler.py security_hardening.py sumsub_client.py` - PASS
- `pytest -q tests/test_screening_queue_state_integrity.py tests/test_screening_queue.py tests/test_screening_review.py tests/test_screening_freshness.py tests/test_approval_gate.py` - PASS, 95 passed
- `pytest -q tests/test_screening_state_priority_a.py tests/test_backoffice_ca_truthflow_static.py tests/test_backoffice_monitoring_navigation_static.py` - PASS, 81 passed
- `pytest -q tests/test_sumsub_hardening_pr14.py tests/test_sumsub_verification.py` - PASS, 58 passed
- `pytest -q tests/test_api.py -k "screening or provider or health or sumsub or kyc"` - PASS, 31 passed, 106 deselected

Authenticated local `/api/screening/queue` smoke:

- Environment: temporary local SQLite test DB with controlled impossible-state fixture.
- Response: HTTP 200.
- Rows returned: 2.
- Required SQ-1 fields missing: 0.
- Impossible-state count: 0.
- Fixture row result: `canonical_status=Review Required`, `officer_label=Review Required`, `screening_provider_status=pending`, `provider_status_scope=aml_pep_sanctions_screening`, `is_terminal=false`, `has_hits=true`, `review_evidence_present=false`, `blocking_flags` includes `unresolved_screening_hits`, `total_hits=1`, `officer_review_status=not_reviewed`, `defensible_clear=false`.

## Browser / Staging

- In-app browser automation was unavailable in this thread because the required browser runtime tool was not exposed.
- Staging validation is pending deployment of this branch/PR.
- Public unauthenticated `https://staging.regmind.co/api/version` returned `401`, so deployed SHA was not confirmed here.

## Remaining Gaps

- Run authenticated staging `/api/version` and `/api/screening/queue` after PR deploy.
- Browser-check staging Screening Queue after deploy and confirm no row shows `Clear` when `terminal=false`, provider status is pending/failed/not configured, or unresolved hits exist.
- If production data repair is later required, use the new audit table for controlled findings before mutating records.

## Final

The implementation meets SQ-1 backend/API/UI integrity requirements locally and adds targeted regression coverage. Staging browser validation remains the only incomplete item because this branch has not been deployed.
