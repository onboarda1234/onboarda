# Workflow Usage Mapping

This PR separates workflow usage from canonical document checks.

The same canonical document policy is reused across workflows:

- `reg_dir` is used by onboarding, director change, and periodic review.
- `passport` is used by onboarding KYC, director change, UBO change, DOB correction, nationality correction, passport expiry replacement, and periodic review.
- `poa` is used by onboarding, address change, and periodic review.
- `certificate_name_change` is the only pilot evidence for company name change and is manual-review-only.

Pilot workflow mappings added to the registry payload:

| Workflow | Required canonical documents | Main blockers | Main triggers |
| --- | --- | --- | --- |
| Onboarding | COI, M&A, register of directors, shareholder/ownership evidence, registered address, board/signatory where required, passport/ID, person POA | KYC submission, memo, approval | Standard document verification |
| Director change | Register of Directors, passport, National ID | Change completion | New/changed director re-screening |
| UBO change | Register of Shareholders, ownership chart, passport, National ID | Change completion | New/changed UBO re-screening |
| Ownership percentage change | Register of Shareholders or ownership evidence | Change completion | Risk recalculation; re-screening if material |
| Address change | Proof of registered address | Change completion | Address/risk consistency refresh |
| Company name change | Certificate of Name Change | Change completion | Entity re-screening; memo staleness; old-to-new name audit |
| DOB correction | Passport | Change completion | Identity continuity review |
| Nationality correction | Passport | Change completion | Re-screening and country-risk refresh if risk changes |
| Passport expiry | Passport | Replacement cannot supersede old passport until verified/manual accepted | Supersession after verification/manual acceptance |
| Periodic review | Updated registers, refreshed address proof, expired replacements, passport, attestation where requested | Periodic review completion | Review evidence freshness |
| EDD basic | SOW, SOF, bank statements, bank reference | EDD closure for active required docs | EDD evidence review |
| Monitoring | Monitoring support evidence | Manual review only in pilot | Case-by-case officer risk refresh |
| Regulatory/source evidence | Regulatory intelligence/source documents | Library-only unless cited/referenced | Source/date/version review when relied upon |

The workflow mapping is present in `runtime_json/document_policy_payload.json`.

