# PR-PROV1 Rollback or Keep Decision

## Status

NO CHANGE MADE.

No staging CA credential switch was performed and no runtime screening request
was sent after operator approval.

## Decision

Keep current staging configuration unchanged for now.

Rationale:

- Staging already reports ComplyAdvantage Mesh active as AML provider with
  fallback disabled.
- API credential hostnames are production-domain.
- ECS backend/worker runtime is aligned to deployed main.
- Dashboard/account mode could not be independently confirmed as Production.
- Prior dashboard evidence reportedly showed Sandbox, so spending screening
  calls before resolving that ambiguity would violate PR-PROV1 safety rules.

## Rollback

Rollback was not required because no switch was performed.

If a future switch is performed and validation fails, use `rollback_plan.md` to
restore the prior task definition / secret version and confirm:

- `/api/version`
- `/api/screening/status`
- ECS backend/worker steady state
- no uncontrolled provider calls after rollback
