# Status Mapping Report

This is a recommendation only. It does not create a state machine or update any
stored status. A mapping is proposed only where the row has supporting workflow
evidence; uncertainty is explicitly left for manual review.

| Current status | Validity | Recommended future status | Mapping disposition | Rows needing manual review |
|---|---|---|---|---|
| dismissed | Valid | dismissed | Manual review required | 3 |
| escalated | Legacy | Manual review required | Manual review required | 1 |
| in_review | Legacy | routed_to_edd | Candidate for future migration | 0 |
| open | Valid | open, routed_to_edd | Manual review required | 9 |
| resolved | Valid | resolved | Manual review required | 1 |

Row-level recommendations and evidence are in `runtime_inventory.json`.
