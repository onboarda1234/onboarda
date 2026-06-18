# PR-ROLE-NAMING-ONBOARDING-OFFICER-1 Changed Label Inventory

Timestamp: 2026-06-18T02:47:54Z

## Scope

This PR is a naming/copy cleanup only. The internal `co` role key remains unchanged. No database role values, permission keys, approval predicates, risk model logic, screening review logic, document verification gates, Agent 1 policy, Change Management, Periodic Review, CA provider logic, SAR/STR, or PR-7 behavior was intentionally changed.

## Changed User-Facing Labels

| Area | Before | After | Files |
| --- | --- | --- | --- |
| Back-office role switcher | `Login as: Compliance Officer` | `Login as: Onboarding Officer` | `arie-backoffice.html` |
| Back-office role switcher | `Login as: Senior CO` | `Login as: Senior Compliance Officer` | `arie-backoffice.html` |
| Roles and permissions table header | `Compliance Officer` | `Onboarding Officer` | `arie-backoffice.html` |
| Roles and permissions table header | `Senior CO` | `Senior Compliance Officer` | `arie-backoffice.html` |
| User management add/edit role options | `Compliance Officer` | `Onboarding Officer` | `arie-backoffice.html` |
| Client-side role display map | `co -> Compliance Officer` | `co -> Onboarding Officer` | `arie-backoffice.html` |
| Client-side role display map | historical literal `Compliance Officer` | resolves to `Onboarding Officer` | `arie-backoffice.html` |
| User profile/sidebar role display | raw role display through `ROLE_LABELS` | `formatRoleLabel(...)` | `arie-backoffice.html` |
| User management role badges | raw role display through `ROLE_LABELS` | `formatRoleLabel(...)` | `arie-backoffice.html` |
| Assignment/reassignment dropdowns | raw role display through `ROLE_LABELS` | `formatRoleLabel(...)` | `arie-backoffice.html` |
| Audit trail table role display | raw `user_role` such as `co` | display label via `formatRoleLabel(...)` | `arie-backoffice.html` |
| Application activity audit card role display | raw `user_role` such as `co` | display label via `formatRoleLabel(...)` | `arie-backoffice.html` |
| Monitoring alert audit role display | raw `user_role` such as `co` | display label via `formatRoleLabel(...)` | `arie-backoffice.html` |
| Internal notes role display | raw `user_role` such as `co` | display label via `formatRoleLabel(...)` | `arie-backoffice.html` |
| Screening false-positive denial copy | `Compliance Officer, SCO, or Admin` | `Onboarding Officer, SCO, or Admin` | `arie-backoffice.html`, `arie-backend/server.py` |
| Directors/UBOs report access copy | `Access restricted to compliance officers` | `Access restricted to onboarding or senior compliance roles` | `arie-backoffice.html` |
| Evidence classification copy | `Compliance officer, SCO, or admin` | `Onboarding Officer, SCO, or admin` | `arie-backoffice.html` |
| Periodic review reminder copy | `assigned compliance officer` | `assigned onboarding officer` | `arie-backoffice.html` |
| Legacy baseline edit copy | `Compliance officer, SCO, or admin` | `Onboarding Officer, SCO, or admin` | `arie-backoffice.html` |
| KPI card label | `CO Override Rate` | `Onboarding Officer Override Rate` | `arie-backoffice.html` |
| Structured-review copy | `checked by a compliance officer` | `checked by an onboarding officer` | `arie-backoffice.html` |
| Backend role display map | `co -> Compliance Officer` | `co -> Onboarding Officer` | `arie-backend/server.py` |
| Backend role display map | `sco -> Senior CO` | `sco -> Senior Compliance Officer` | `arie-backend/server.py` |
| `/api/config/roles-permissions` display labels | `Compliance Officer`, `Senior CO` | `Onboarding Officer`, `Senior Compliance Officer` | `arie-backend/server.py` |
| Governance attempt denial copy | `Compliance Officer` / `Senior CO` | `Onboarding Officer` / `Senior Compliance Officer` | `arie-backend/server.py` |
| Regulatory Intelligence fallback actor copy | `Compliance Officer` | `Onboarding Officer` | `arie-backend/server.py` |
| IDV resolution role-denial copy | `Only CO, SCO, or Admin...` | `Only Onboarding Officer, SCO, or Admin...` | `arie-backend/server.py` |

## Intentionally Unchanged

| Match | Reason |
| --- | --- |
| `co` role key in backend predicates, tests, fixtures, token generation, and permission lists | Internal key must remain unchanged. |
| `sco` role key and `SCO` abbreviation | Senior Compliance Officer/SCO wording must remain unchanged. |
| `Senior Compliance Officer` user-facing labels | Explicitly out of scope to rename. |
| `Escalate to Senior CO` permission label | Existing SCO wording retained; not a `co` role label. |
| `CO` in comments, test fixture IDs, app refs, and test user names | Not user-facing product copy. |
| Generic `compliance officer` copy in portal/product/legal explanatory text | Not a specific `co` role label in the back-office surfaces for this PR. |
| Historical audit records | Not rewritten. Display resolution only. |

## Search Evidence

Commands used during implementation:

```bash
rg -n --hidden -S "Compliance Officer|compliance officer|Senior Compliance Officer|\\bSCO\\b|\\bCO\\b" -g '!node_modules' -g '!vendor' -g '!dist' -g '!build' -g '!coverage' -g '!playwright-report' -g '!test-results' -g '!docs/audits/evidence/**'
rg -n -S "Compliance Officer|compliance officer|compliance officers|\\bCO\\b|Senior CO|Onboarding Officer" arie-backoffice.html arie-backend/server.py arie-backend/tests
```
