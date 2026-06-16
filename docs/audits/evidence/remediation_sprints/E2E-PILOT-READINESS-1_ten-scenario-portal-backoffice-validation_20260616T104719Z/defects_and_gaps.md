# Defects And Gaps

| Severity | Key | Evidence | Impact |
| --- | --- | --- | --- |
| P0 | prescreening_smoke_503 | Smoke app `ARF-2026-900303` portal submit returned 503 twice: `Screening provider temporarily unavailable. Please retry in a moment.` | Blocks all portal-to-back-office E2E validation and pilot readiness |
| P2 | provider_workspace_not_api_visible | `/api/screening/status` confirms CA active/fallback disabled but does not expose CA workspace identifier | Sandbox confirmation depends on operator statement plus smoke behavior, not a directly visible API field |

No product logic was changed in this audit.
