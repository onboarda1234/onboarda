# PR-1B Root Cause

The PR-1 internal API boundary fix correctly denied client access to officer-only application, screening, memo, supervisor, audit, provider, and IDV routes. It did not cover client notification projection.

Root causes:

1. `GET /api/notifications` returned stored `client_notifications` rows without role-aware client-safe projection.
2. Pre-approval request-for-information flow embedded officer notes in the client-facing notification message.
3. Structured RMI creation mirrored officer-supplied `reason` into a client notification and returned full RMI rows to the client, including fields and wording intended for back-office context.
4. Existing unsafe rows in `client_notifications` and `rmi_requests` needed read-time sanitization, not only future insert cleanup.

Fix strategy:

- Add centralized client notification and RMI projection helpers.
- Use canonical safe copy for known client notification types.
- Allow unknown legacy notifications only when title/message text passes unsafe-pattern screening.
- Sanitize legacy `documents_list`, RMI `reason`, RMI item labels/descriptions, and suppress creator metadata for client responses.
- Update known notification creation paths to store safe client-facing copy going forward.
