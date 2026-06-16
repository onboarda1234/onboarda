# Recommendations

1. Create a corrective PR for the staging prescreening provider path: aligned staging reports CA active and fallback disabled, but portal prescreening submit still returns 503.
2. Add a non-secret CA runtime descriptor to `/api/screening/status`, such as `workspace_mode: sandbox` and the active screening configuration identifier label, so future audits can confirm Sandbox without relying only on operator confirmation.
3. After the corrective PR is deployed, rerun this exact gate sequence before any ten-scenario E2E:
   - source-of-truth version gate
   - deployment alignment gate
   - provider status gate
   - one clean prescreening smoke
   - ten-scenario E2E only after the smoke passes

Recommended next PR: `PR-CA-SANDBOX-PRESCREENING-503`.
