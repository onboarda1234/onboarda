# Role Verification

Local automated verification:
- Backend disabled endpoint tests authenticate as admin and compliance officer.
- Existing auth/export suites preserve admin/SCO/CO/client role checks.
- Supervisor Audit export enabled-mode tests explicitly opt in to the enterprise flags for coverage; pilot-disabled tests verify inactive defaults.

Limitations:
- Staging role-by-role browser verification is pending until merged staging deploy and authenticated access.
- If only one staging authenticated role is available, closure must document that limitation and rely on static/backend route verification for remaining roles.

