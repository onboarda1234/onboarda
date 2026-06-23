# Changed Files Summary

Product/UI:
- `arie-portal.html`
  - Reparents the Periodic Review Attestation modal to `document.body` before opening so it escapes the `.app` stacking context and cannot sit behind the fixed sidebar.
  - Raises the modal layer above sidebar/navigation.
  - Locks background scroll while the modal is open.
  - Keeps the modal header and Close button visible.
  - Moves vertical scrolling into the modal body.
  - Adds viewport-safe width, max-height, and overflow constraints to prevent horizontal clipping/page overflow.

Tests:
- `arie-backend/tests/test_portal_periodic_review_attestation_static.py`
  - Adds a static regression check for the viewport-scoped modal layer, body scroll lock, body reparenting, and modal body scrolling.

Evidence:
- `docs/audits/evidence/remediation_sprints/PR-PRS-PORTAL-ATTESTATION-MODAL-LAYOUT-FIX-1_20260623T023801Z/`
  - Browser smoke report, console/network summary, screenshots, raw smoke JSON, and closure report.

Out of scope and unchanged:
- Periodic Review business logic.
- Questions and document requirements.
- Backend APIs.
- Review statuses.
- Agent 1 logic.
- Change Management.
- Onboarding/KYC flows.
