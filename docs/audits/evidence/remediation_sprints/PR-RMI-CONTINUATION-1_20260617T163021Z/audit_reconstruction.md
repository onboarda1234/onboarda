# Audit Reconstruction

New audit events and linkage:

- `RMI Replacement Uploaded`
  - Records application id/ref, replacement document id, doc type, person id/type, RMI request id, RMI item id, RMI trace slot `rmi:<item_id>`, canonical slot key, stored slot key, actor, and timestamp.
  - Uses `before_state` from the RMI item and `after_state` from the stored document mapping.

- `RMI Continuation Blocked`
  - Records attempted target status and blocker reasons when a fulfilled-RMI or document-gate condition is not met.
  - Captures `before_state` and rejected attempted `after_state`.

- `RMI Continuation`
  - Records valid continuation from `rmi_sent` to the approved next workflow status.
  - Captures request ids and before/after workflow state.

Local API test evidence:
- `test_fulfilled_rmi_sent_can_continue_to_kyc_documents_and_audits` asserts `RMI Continuation` exists and includes before state `rmi_sent` and after state `kyc_documents`.
- `test_rmi_replacement_upload_maps_to_canonical_required_slot` asserts `RMI Replacement Uploaded` contains both the RMI request id and RMI item id.

Staging audit extraction: pending PR merge and deploy.
