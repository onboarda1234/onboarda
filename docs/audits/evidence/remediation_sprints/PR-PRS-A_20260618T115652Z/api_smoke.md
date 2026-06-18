# PR-PRS-A API Smoke

- Base URL: `http://127.0.0.1:10000`
- Transient DB path during run: `/Users/Aisha/CodexWork/onboarda-pr-prs-a/docs/audits/evidence/remediation_sprints/PR-PRS-A_20260618T115652Z/logs/pr_prs_a_smoke.db` (removed after capture; raw API output retained below)
- Authenticated user: `Raj Patel` (`sco`)
- Synthetic prefix: `PRPRS-A-FINAL`

## Scenario Results

- `default_queue_actionable_only`: PASS
- `completion_next_cycle_anniversary_anchor`: PASS
- `completed_reviews_frozen`: PASS
- `legacy_decision_canonical_gates`: PASS
- `edd_awaiting_and_feedback_completion`: PASS

## Details

```json
{
  "completed_reviews_frozen": {
    "errors": {
      "evidence_link": "periodic_review id=9 is completed and cannot be modified",
      "findings": "periodic_review id=9 is completed and cannot be modified",
      "rationale": "periodic_review id=9 is completed and cannot be modified",
      "risk_change": "periodic_review id=9 is completed and cannot be modified"
    },
    "passed": true,
    "review_id": 9,
    "statuses": {
      "evidence_link": 409,
      "findings": 409,
      "rationale": 409,
      "risk_change": 409
    }
  },
  "completion_next_cycle_anniversary_anchor": {
    "first_audit": {
      "anchor_date": "2025-01-01",
      "application_id": "prprs-a-final-anchor",
      "calculation_basis": "risk_level:HIGH",
      "completed_review_id": 4,
      "completion_date": "2026-06-18",
      "current_due_date": "2026-01-01",
      "frequency_months": 12,
      "late_completion_days": 168,
      "next_review_date": "2027-01-01",
      "next_review_id": 5,
      "skipped_anniversary_count": 0
    },
    "first_completion_status": "periodic_review_completed",
    "first_next_cycle": {
      "anchor_date": "2025-01-01",
      "calculation_basis": "risk_level:HIGH",
      "due_date": "2027-01-01",
      "frequency_months": 12,
      "late_completion_days": 168,
      "next_review_date": "2027-01-01",
      "periodic_review_id": 5,
      "policy_version": "v2",
      "review_cycle_number": 2,
      "risk_level": "HIGH",
      "skipped_anniversary_count": 0,
      "status": "created"
    },
    "first_review_id": 4,
    "passed": true,
    "recompletion_status": 409,
    "schedule_dates": [
      "2027-01-01",
      "2028-01-01"
    ],
    "second_next_cycle": {
      "anchor_date": "2025-01-01",
      "calculation_basis": "risk_level:HIGH",
      "due_date": "2028-01-01",
      "frequency_months": 12,
      "late_completion_days": null,
      "next_review_date": "2028-01-01",
      "periodic_review_id": 6,
      "policy_version": "v2",
      "review_cycle_number": 3,
      "risk_level": "HIGH",
      "skipped_anniversary_count": 0,
      "status": "created"
    },
    "second_review_id": 5,
    "skip_audit": {
      "anchor_date": "2023-01-01",
      "application_id": "prprs-a-final-skip",
      "calculation_basis": "risk_level:HIGH",
      "completed_review_id": 7,
      "completion_date": "2026-06-18",
      "current_due_date": "2024-01-01",
      "frequency_months": 12,
      "late_completion_days": 899,
      "next_review_date": "2027-01-01",
      "next_review_id": 8,
      "skipped_anniversary_count": 2
    },
    "skip_next_cycle": {
      "anchor_date": "2023-01-01",
      "calculation_basis": "risk_level:HIGH",
      "due_date": "2027-01-01",
      "frequency_months": 12,
      "late_completion_days": 899,
      "next_review_date": "2027-01-01",
      "periodic_review_id": 8,
      "policy_version": "v2",
      "review_cycle_number": 2,
      "risk_level": "HIGH",
      "skipped_anniversary_count": 2,
      "status": "created"
    },
    "skip_review_id": 7
  },
  "default_queue_actionable_only": {
    "cancelled_review_id": 3,
    "completed_filter_contains": [
      2
    ],
    "completed_review_id": 2,
    "default_contains": [
      1
    ],
    "passed": true,
    "pending_review_id": 1
  },
  "edd_awaiting_and_feedback_completion": {
    "approval_status": 200,
    "awaiting_row": {
      "closed_at": null,
      "completed_at": null,
      "linked_edd_case_id": 1,
      "status": "awaiting_edd"
    },
    "awaiting_status": "awaiting_edd",
    "edd_case_id": 1,
    "edd_row": {
      "decision": "edd_approved",
      "linked_periodic_review_id": 14,
      "stage": "edd_approved"
    },
    "final_row": {
      "closed_at": "2026-06-18T12:11:52+00:00",
      "completed_at": "2026-06-18T12:11:52+00:00",
      "linked_edd_case_id": 1,
      "outcome": "edd_required",
      "status": "completed"
    },
    "next_cycle": {
      "due_date": "2027-01-01",
      "id": 15,
      "next_review_date": "2027-01-01",
      "review_cycle_number": 2,
      "status": "pending"
    },
    "passed": true,
    "review_id": 14
  },
  "legacy_decision_canonical_gates": {
    "blocked_review_id": 11,
    "blocked_row": {
      "decision": null,
      "outcome": null,
      "status": "in_progress"
    },
    "blocked_status": 409,
    "blocking_items": [
      {
        "completion_only": false,
        "item_type": "client_attestation_required",
        "label": "Client attestation has not been submitted",
        "severity": "high",
        "source": "periodic_reviews",
        "source_id": 11
      }
    ],
    "clean_review_id": 12,
    "clean_row": {
      "closed_at": "2026-06-18T12:11:52+00:00",
      "decision": null,
      "next_review_date": "2027-01-01",
      "outcome": "no_change",
      "status": "completed"
    },
    "clean_status": 200,
    "passed": true
  }
}
```
