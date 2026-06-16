# PR-DOC-RECON-1 Diagnosis

Base `origin/main` SHA: `0071f09a3bc1eb805f99f6c22c2674913868c78e`

Precondition confirmed: latest `origin/main` contains PR #501 (`69051182c880897343e7c0c87d8ac4e52e5b36c4`) and PR #500 (`07c992d7716183226d53f70bf0d01bf7e87da874`).

## Current-State Findings

- The portal upload flow is already working and remains slot-driven. This PR does not redesign or change the normal onboarding portal document upload UI.
- The Document Verification Policies settings page still exposed the PR-DOC2A architecture/registry dashboard (`Agent 1 Evidence Control Layer`, `Document Policy Registry`, `Canonical Policy Coverage`, registry metrics, lifecycle filters) above the useful check editor. This made the page look like an architecture dashboard rather than an admin verification-check configuration page.
- Enhanced Requirement document upload paths used the generic `enhanced_requirement` document type and did not queue Agent 1 verification. This prevented SOW/SOF/bank reference/EDD requested evidence from aligning with canonical document policies and caused runtime behavior to lag behind settings.
- AI Agents -> Agent 1 wording still needed to match the simplified current active scope: uploaded onboarding documents and requested evidence documents verified through Document Verification Policies. Agent 1 must not imply sanctions/PEP/adverse-media screening ownership.
- Application Review A/B/C/D/E/F/G document sections were already present and covered by existing regression tests. No redesign was required in this PR.

## Root Cause

The product had two competing representations:

1. A canonical policy registry/state model added by PR #500.
2. A legacy settings editor that persisted check configuration through `/config/verification-checks`.

PR-DOC2A made the registry visible in settings. PR-DOC-RECON-1 product direction requires settings to show the actual editable verification-check configuration, not the architecture dashboard. Enhanced Requirement uploads also needed to map requested evidence to canonical policy keys at runtime instead of using the catch-all `enhanced_requirement` type.

## Scope Decision

- Onboarding portal upload behavior is preserved.
- Enhanced Requirement upload behavior is reconciled for current pilot EDD/requested-evidence documents.
- Change-management enforcement, periodic-review enforcement, and SAR/STR activation remain out of scope.
- Manual-review-only and future/enterprise evidence is not presented as active runtime verified.
