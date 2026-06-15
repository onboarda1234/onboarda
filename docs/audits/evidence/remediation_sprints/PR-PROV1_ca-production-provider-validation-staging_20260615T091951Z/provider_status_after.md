# PR-PROV1 Provider Status After Switch

## Status

NOT RUN.

No PR-PROV1 credential switch was performed because explicit operator approval, controlled test subjects, and test cap/cost approval have not yet been provided.

## Required After Approval

After an approved switch or confirmation to keep the current production-domain credential mode:

1. Restart/deploy staging.
2. Confirm `/api/version` still matches the deployed main SHA.
3. Confirm `/api/screening/status` shows:
   - AML provider: ComplyAdvantage Mesh
   - fallback/simulation disabled
   - Sumsub remains IDV/KYC only
   - OpenCorporates/registry remains separate
4. Save redacted JSON evidence to `runtime_json/screening_status_after.json`.

## Current Result

Status remains `READY TO SWITCH / AWAITING APPROVAL`.
