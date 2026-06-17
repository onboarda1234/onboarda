# Role Access Matrix

Status: implementation tests passed; staging smoke pending.

Sensitive config surfaces covered:

| Surface | Read roles | Mutate roles | Analyst/read-only behavior | Evidence |
|---|---|---|---|---|
| `/api/config/risk-model` | admin, SCO, CO, analyst | admin | GET allowed, PUT returns 403 and logs `authz_denied_internal_api` | `TestAdminPilotMutationAuditabilityAndRBAC` |
| `/api/config/country-risk` | admin, SCO, CO, analyst | No direct mutation endpoint | GET allowed | `TestAdminPilotMutationAuditabilityAndRBAC` |
| `/api/config/ai-agents` | admin, SCO, CO, analyst | admin | GET allowed, POST returns 403 and logs denial on protected write path | `TestAdminPilotMutationAuditabilityAndRBAC` |
| `/api/config/ai-agents/:id` | N/A | admin | PUT/DELETE return 403 and log denial | `TestAdminPilotMutationAuditabilityAndRBAC` |
| `/api/config/verification-checks` | admin, SCO, CO, analyst | admin | GET allowed, PUT returns 403 | `TestAdminPilotMutationAuditabilityAndRBAC` |
| `/api/config/system-settings` | admin, SCO, CO, analyst | admin | PUT returns 403 for lower roles | `TestAdminPilotMutationAuditabilityAndRBAC` |
| `/api/config/document-policies` | admin, SCO, CO, analyst | No mutation endpoint | GET allowed only for backoffice roles | Handler uses `require_backoffice_auth` |

Existing enhanced requirement settings remain outside this PR's sensitive config mutation change: reads are admin/SCO/CO and writes are admin/SCO per existing policy.
