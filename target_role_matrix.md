# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Target Role Matrix (Design)

## Operating model to enforce
- Onboarding Officer (`co`) can approve LOW/MEDIUM **clean** files.
- Onboarding Officer cannot approve HIGH/VERY_HIGH.
- Onboarding Officer cannot approve PEP, material screening hits, EDD, unresolved second review, or override cases.
- SCO can approve high-risk/escalated files after all gates are clear.
- Admin authority is explicit and bounded.
- Analyst cannot approve.

## Target capability matrix

Legend: ✅ allowed, ⚠️ conditional, ❌ blocked

| Capability | admin | sco | co (Onboarding Officer) | analyst | client | Target rule |
|---|---:|---:|---:|---:|---:|---|
| Final Approve | ⚠️ | ✅ | ⚠️ | ❌ | ❌ | co allowed only LOW/MEDIUM + clean gates |
| Final Reject | ⚠️ | ✅ | ✅ | ❌ | ❌ | reject remains officer decision; analyst excluded |
| Submit to Compliance | ⚠️ | ✅ | ✅ | ❌ | ❌ | package handoff to SCO queue; not final decision |
| Override AI / blocker acceptance | ⚠️ | ✅ | ❌ | ❌ | ❌ | override remains senior-only control |
| Screening second review | ⚠️ | ✅ | ❌ | ❌ | ❌ | keep existing protection |
| Memo approval | ⚠️ | ✅ | ❌ | ❌ | ❌ | keep as senior-only unless policy changes separately |
| Assignment/reassignment | ⚠️ | ✅ | ❌ | ❌ | ❌ | retain admin/sco only |
| Export evidence pack | ⚠️ | ✅ | ❌ | ❌ | ❌ | retain admin/sco only |

## Explicit admin policy (target)
- Admin can act as emergency/supervisory authority for final decisions and overrides.
- Admin actions must be fully audited with reason fields equal to SCO requirements.
- Admin should not silently bypass mandatory gates (screening second-review, EDD, memo/document gates).
