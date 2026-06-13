# PR-1 Root Cause - FSI-001

## Root Cause

Internal compliance APIs were using authentication-only gates where role-aware authorization was required.

The main cause was not token validation. Active client tokens were valid and correctly revalidated against the database. The defect was that selected internal handlers accepted any authenticated actor:

- `/api/applications` used `require_auth()` instead of a back-office-only guard.
- `/api/screening/queue` used `require_auth()` instead of a back-office-only guard.
- `/api/screening/status` used `require_auth()` instead of a back-office-only guard.

The detail endpoint also relied on a top-level client-safe projection while leaving nested structures to evolve independently. That made future leakage likely and left document review metadata and prescreening provider/screening structures outside the explicit fail-closed boundary.

## Corrective Design

- Add a reusable `require_backoffice_auth()` helper to `BaseHandler`.
- Use the helper for internal application list, screening queue, and provider status endpoints.
- Keep the existing portal-safe client list endpoint and apply canonical fixture exclusion there.
- Keep owned application detail available to clients, but harden the projection to explicitly strip nested document review metadata, provider diagnostics, screening reports, IDV gate data, memo/gate fields, and internal risk/decision fields.
- Add regression tests that prove client denial, back-office access, ownership enforcement, and portal-safe projections.
