# PR-RMI-CONTINUATION-1 Case 8 Regression

Status: local regression passed; staging regression pending merge and deploy.

Original failing application: `ARF-2026-900343`

Confirmed defects addressed:
- `rmi_sent` had no allowed continuation transitions and behaved as terminal.
- Replacement Shareholder Register uploaded with `doc_type=reg_sh` was stored only under `slot_key=rmi:<item_id>`, while the document evidence and memo gates required `entity:reg_sh`.

Implementation checks:
- New RMI replacement uploads tied to canonical mandatory slots now store the document under the canonical slot, for example `entity:reg_sh`, while preserving `rmi_item_id`, `rmi_request_id`, and `rmi:<item_id>` trace metadata.
- Existing active `rmi:<item_id>` rows are aliased by the document reliance gate to the canonical slot when the RMI item unambiguously maps to a required KYC expectation.
- `rmi_sent` can continue only to `kyc_documents`, `kyc_submitted`, or `compliance_review`.
- Continuation requires all active RMI items to be accepted and linked to a document.
- Direct continuation to `kyc_submitted` or `compliance_review` also requires the document evidence gate to pass.
- Arbitrary transitions from `rmi_sent` remain rejected.

Local regression tests:
- `test_rmi_replacement_alias_satisfies_canonical_required_slot`
- `test_rejected_rmi_replacement_does_not_satisfy_canonical_slot`
- `test_memo_generation_stage_sees_rmi_replacement_alias`
- `test_rmi_replacement_upload_maps_to_canonical_required_slot`
- `test_unfulfilled_rmi_sent_cannot_continue`
- `test_fulfilled_rmi_sent_can_continue_to_kyc_documents_and_audits`
- `test_rmi_sent_direct_kyc_submitted_still_requires_document_gate`
- `test_rmi_sent_rejects_arbitrary_status_transition`

Staging Case 8 replay: pending PR merge and deploy.
