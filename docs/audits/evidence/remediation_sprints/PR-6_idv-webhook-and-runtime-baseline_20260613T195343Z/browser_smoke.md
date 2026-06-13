# PR-6 Browser Smoke

Branch-stage browser smoke: not applicable.

Reason:

- PR-6 does not change client portal UI.
- PR-6 does not change back-office IDV UI rendering.
- PR-6 changes server-side webhook/runtime contracts, deployment workflow, runtime evidence helpers, and worker smoke helpers.

Required after merge:

- If staging API/runtime smoke shows no client/officer-visible IDV state changes, browser smoke may remain not applicable.
- If webhook or worker smoke changes visible IDV/document verification state, run browser smoke for:
  - back-office IDV/document verification area;
  - client portal document/status area;
  - no internal provider diagnostics leaked to client users.
