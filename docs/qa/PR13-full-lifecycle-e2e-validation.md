# PR13 Full Lifecycle E2E Validation

Date: 2026-05-19

## Verdict

Pass. The full periodic-review lifecycle path was validated on AWS staging with GitHub `main` and deployed runtime both at `33ae6e3371dc3a253df3ecc14180560a1c3eb83f`.

No P0/P1/P2 defects were found. The roadmap may proceed to PR14.

## Environment

| Item | Evidence |
|---|---|
| GitHub main SHA | `33ae6e3371dc3a253df3ecc14180560a1c3eb83f` |
| Deployed SHA | `33ae6e3371dc3a253df3ecc14180560a1c3eb83f` |
| ECS task definition | `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:301` |
| ECR image | `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:33ae6e3371dc3a253df3ecc14180560a1c3eb83f` |
| API version | `/api/version` returned matching `git_sha` and `image_tag` |
| Health | `/api/health` returned `200`, `status=ok`, `environment=staging` |
| Liveness | `/api/liveness` returned `200`, `status=ok` |
| Staging URL | `https://staging.regmind.co` |

## Scenario Evidence

| Step | Expected | Actual | Result |
|---|---|---|---|
| Existing client/review | Approved client with pending review exists | Application `e6d43e0424fd4d51`, review `27` selected | Pass |
| Last review date | Last review date entered and stored | `last_review_date=2026-05-01` | Pass |
| Next review calculation | Risk policy stored on review | Initial LOW policy stored, then review-level MEDIUM risk change recalculated `next_review_date=2028-05-01`, `frequency_months=24`, `policy_version=v1` | Pass |
| Assignment | Review assigned to officer | `assigned_officer=co001` | Pass |
| Start review | Review enters active workspace | `status=in_progress`; required checklist generated | Pass |
| Material-change attestation | Officer-owned attestation persists | `material_change_attestation=no_material_change` | Pass |
| Risk-change attestation | Review-level risk change recorded without changing application risk | Review records MEDIUM; response/audit state `application_risk_write_status=unsafe_gap`; application risk remains LOW | Pass |
| System panels | KYC/screening/alerts source from existing projections | Required items generated for profile, ownership, jurisdiction, screening, risk, and outcome | Pass |
| Evidence requirement | Custom evidence request can be added | `custom_evidence_requirement:27:1` added and cleared | Pass |
| Evidence link | Existing document is linked, not duplicated | Existing document `47ac9b6d22be4857` linked; documents count remains 9 | Pass |
| EDD linkage | EDD case linked when outcome requires it | EDD case `217` created with `origin_context=periodic_review` and reverse link to review `27` | Pass |
| Rationale | Officer rationale required and persisted | Rationale saved before completion | Pass |
| Outcome | Completion writes canonical outcome, not legacy decision | `outcome=edd_required`; `decision=null` | Pass |
| Memo | Periodic-review memo generated separately | `memo_status=generated`, `periodic_review_memo_id=10` | Pass |
| History | Completed review appears in lifecycle history | `/api/lifecycle/queue?include=historical` includes review `27` for application `e6d43e0424fd4d51` | Pass |

## Evidence Artifacts

API/audit final report:

- `/tmp/pr13-e2e-final-report.json`
- Checks failed: none
- Audit entries for review: 20

Browser smoke report:

- `/tmp/pr13-browser-smoke/report.json`
- Screenshots captured: 12
- Blocking console errors: 0
- Page errors: 0
- Failed requests: 0
- Unexpected API responses: 0

Screenshots:

- `/tmp/pr13-browser-smoke/applications.png`
- `/tmp/pr13-browser-smoke/application-detail-lifecycle.png`
- `/tmp/pr13-browser-smoke/application-detail-kyc-docs.png`
- `/tmp/pr13-browser-smoke/application-detail-screening.png`
- `/tmp/pr13-browser-smoke/application-detail-supervisor.png`
- `/tmp/pr13-browser-smoke/application-detail-activity.png`
- `/tmp/pr13-browser-smoke/case-management.png`
- `/tmp/pr13-browser-smoke/ongoing-monitoring-alerts.png`
- `/tmp/pr13-browser-smoke/ongoing-monitoring-agents.png`
- `/tmp/pr13-browser-smoke/lifecycle-queue.png`
- `/tmp/pr13-browser-smoke/edd.png`
- `/tmp/pr13-browser-smoke/change-management.png`

Read-only DB evidence from ECS task `8c4081c2e527457c919c807d9d4577a6`:

```json
{
  "review": {
    "id": 27,
    "application_id": "e6d43e0424fd4d51",
    "status": "completed",
    "outcome": "edd_required",
    "decision": null,
    "last_review_date": "2026-05-01",
    "next_review_date": "2028-05-01",
    "policy_version": "v1",
    "frequency_months": 24,
    "linked_edd_case_id": 217,
    "memo_status": "generated",
    "periodic_review_memo_id": 10
  },
  "evidence_links_count": 2,
  "documents_count": 9,
  "edd": {
    "id": 217,
    "application_id": "e6d43e0424fd4d51",
    "stage": "triggered",
    "origin_context": "periodic_review",
    "linked_periodic_review_id": 27
  }
}
```

## Audit Evidence

The audit endpoint returned entries for the following periodic-review actions:

- `periodic_review.legacy_import_saved`
- `periodic_review.assignment_updated`
- `periodic_review.state_changed`
- `periodic_review.required_items.generated`
- `periodic_review.material_change_attested`
- `periodic_review.risk_rerated`
- `periodic_review.required_item.added`
- `periodic_review.evidence_link_added`
- `periodic_review.required_item.updated`
- `periodic_review.escalated_to_edd`
- `periodic_review.officer_rationale_saved`
- `lifecycle.review.closed`
- `periodic_review.outcome_recorded`

At least one audit row contains both `before_state` and `after_state`; mutating periodic-review actions identify `periodic_review:27`.

## Notes

Risk change was replayed once during validation-script resume after a non-product assertion error. The first audit row records the material review-level change from LOW to MEDIUM. The later replay records MEDIUM to MEDIUM. Final application risk remains LOW, which confirms the inline review risk action did not mutate authoritative application risk.

EDD case `217` remains active in the EDD owner workflow because the periodic-review outcome is `edd_required`. Lifecycle correctly links to the EDD case; EDD processing remains owned by EDD, not Lifecycle.

## Follow-Ups

Phase 3 integration:

- A dedicated reusable PR13 E2E runner could be promoted from the temporary validation script if the team wants this exact scenario to become a recurring staging job.

Nice-to-have:

- Add clearer API response vocabulary for repeated no-op risk re-rate attempts so validation scripts can distinguish first-time re-rates from replayed same-risk attestations.

Production blocker:

- None.
