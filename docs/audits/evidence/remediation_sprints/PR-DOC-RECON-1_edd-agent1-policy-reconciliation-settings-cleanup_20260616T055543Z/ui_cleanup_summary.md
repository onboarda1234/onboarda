# UI Cleanup Summary

User intent evidence:

- `/Users/Aisha/Downloads/WhatsApp Image 2026-06-16 at 09.27.00.jpeg`
- `/Users/Aisha/Desktop/Screenshot 2026-06-16 at 9.35.45 AM.png`
- `/Users/Aisha/Desktop/Screenshot 2026-06-16 at 9.35.56 AM.png`

## Document Verification Policies

Changed the page from architecture-first to configuration-first.

Removed from the visible settings page:

- `Agent 1 Evidence Control Layer`
- `Document Policy Registry`
- `Canonical Policy Coverage`
- policy count metric cards
- lifecycle/gate/status/search registry filters
- registry policy cards

Kept:

- `Document Verification Policies`
- `Underlying Verification Check Configuration`
- `Entity Documents`
- `Person / KYC Documents`
- `Enhanced / EDD Documents`
- existing save behavior through `/config/verification-checks`

## AI Agents -> Agent 1

Aligned copy:

> Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured in Document Verification Policies. It can verify, flag, block reliance, recommend officer action, and trigger required follow-up. It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening.

The pipeline now describes configured checks rather than active policy registry counts.

## Application Review

No broad redesign in this PR. Existing sections remain:

- A - Corporate Entity Documents
- B - Directors & UBO Identity Documents
- C - Enhanced Evidence Documents
- D - Other Documents
- E - Portal Disclosures
- F - Internal Controls
- G - Verification History

View/Download and action-first rows from PR-DOC-UI-1 are preserved.
