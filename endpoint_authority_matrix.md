# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Endpoint Authority Matrix (origin/main)

## Decision / approval / rejection paths

| Method | Path | Authority-sensitive action | Allowed roles (backend) | Notes |
|---|---|---|---|---|
| POST | `/api/applications/:id/decision` | approve/reject/escalate_edd/request_documents | admin, sco, co | Canonical decision endpoint; `override_ai` requires admin/sco |
| PATCH | `/api/applications/:id` | direct status mutation, including `approved`/`rejected` | any non-client officer after ownership check | **Bypass risk:** no explicit role gate for final approve/reject |
| POST | `/api/applications/:id/pre-approval-decision` | pre-screen reject / pre-approve / request info | admin, sco | High-risk pre-approval flow |
| PATCH | `/api/edd/cases/:id` | `edd_approved` / `edd_rejected` closure | admin, sco, co (but closure enforced admin/sco) | Closure requires senior role + dual-control checks |

## Memo approval path

| Method | Path | Action | Allowed roles | Notes |
|---|---|---|---|---|
| POST | `/api/applications/:id/memo/approve` | approve memo | admin, sco | Includes validation/supervisor/document/staleness gates |

## Override path

| Method | Path | Action | Allowed roles | Notes |
|---|---|---|---|---|
| POST | `/api/applications/:id/decision` | `override_ai=true` | admin, sco | `override_reason` + officer signoff mandatory |
| POST | `/api/documents/:id/review` | manual accept unverified evidence | admin, sco | co/analyst can review, but governed acceptance is senior-only |

## Screening review completion

| Method | Path | Action | Allowed roles | Notes |
|---|---|---|---|---|
| POST | `/api/screening/review` | first/second screening disposition | admin, sco, co, analyst | second-review specifically enforced admin/sco only |

## Assignment changes

| Method | Path | Action | Allowed roles | Notes |
|---|---|---|---|---|
| PATCH | `/api/applications/:id` | assign/reassign application | admin, sco | `reassignment_reason` required |
| POST | `/api/monitoring/reviews/:id/assignment` | assign/reassign periodic review | admin, sco, co | periodic-review assignment scope |

## Role/config/settings changes

| Method | Path | Action | Allowed roles |
|---|---|---|---|
| GET/POST | `/api/users` | list/create users | GET: admin,sco; POST: admin |
| PUT | `/api/users/:id` | update role/status | admin |
| PUT | `/api/config/risk-model` | edit risk model | admin |
| PUT | `/api/config/system-settings` | edit system settings | admin |
| POST | `/api/config/ai-agents` | create AI agent config | admin |
| PUT/DELETE | `/api/config/ai-agents/:id` | modify/delete AI agent config | admin |
| PUT | `/api/config/verification-checks` | edit verification checks | admin |
| POST/PATCH/enable-disable | `/api/settings/enhanced-requirements*` | edit enhanced-review rule config | admin, sco |

## Endpoint findings
- Critical final decision authority is split between `/decision` (strict) and `/applications/:id` PATCH (lenient). This is the primary authority gap.
- No endpoint currently exists for `submitted_to_compliance` handoff.
