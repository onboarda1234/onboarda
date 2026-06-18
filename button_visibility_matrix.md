# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Button Visibility Matrix (current UI)

Source: `/home/runner/work/onboarda/onboarda/arie-backoffice.html`

| Button / action | Where shown | Visibility condition | Permission check timing | Backend authority check |
|---|---|---|---|---|
| Approve (`btn-approve`) | Application detail standard actions | Shown whenever not in pre-approval card | On click (`assertPermission('approve_low_medium')`) | `/applications/:id/decision` role-gated; PATCH path still exists |
| Reject (`btn-reject`) | Application detail standard actions | Same as above | On click (`reject_applications`) | `/applications/:id/decision` role-gated; PATCH path still exists |
| More Info (`btn-rmi`) | Application detail standard actions | Same as above | On click (`request_more_information`) | `/applications/:id/decision` (`request_documents`) |
| Override (`btn-override`) | Application detail standard actions | Same as above | On click (`override_ai_risk_score`) | `/applications/:id/decision` override senior-only |
| Escalate (`escalateCase`) | Application detail standard actions | Same as above | On click (`escalate_to_sco`) | `/applications/:id/decision` (`escalate_edd`) |
| Reassign (`btn-reassign`) | Application detail standard actions | Hidden unless `assign_reassign_cases` | On click too | PATCH `/applications/:id` admin/sco only |
| Export Pack (`btn-export-pack`) | Application detail standard actions | Hidden unless role admin/sco helper | On click too | `/applications/:id/export-pack` admin/sco only |
| Approve Memo (`btn-approve-memo`) | Memo panel | Visible in memo panel | On click uses `approve_low_medium` in UI | Backend `/memo/approve` admin/sco only |
| Continue Review (`btn-rmi-continue`) | RMI section | Shown when app status `rmi_sent` and all RMI items fulfilled | Immediate action | PATCH `/applications/:id` status transition |
| Submit Resolution (`idv-resolution-submit`) | IDV modal | Modal-based | On submit validations | `/kyc/identity-verifications/resolve` with senior checks for sensitive outcomes |
| Screening queue actions (Clear/Confirm/Escalate/Request Info) | Screening queue table and detail panel | Row must be review-required/actionable | Some checks before submit (clear permission), others rely on backend | `/screening/review` enforces second-review and disposition rules |

## UI behavior notes
- Standard decision buttons are mostly visible by status context, not by role-specific hide rules.
- Several authority denials happen only after click/submit, creating operator ambiguity.
- No “Submit to Compliance” button exists.
