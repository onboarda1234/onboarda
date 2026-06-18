# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Audit Findings

## P0 (critical)

### P0-1: Final decision bypass via generic application PATCH
- Endpoint: `PATCH /api/applications/:id`
- Issue: endpoint permits direct `status` mutation to `approved` / `rejected` without explicit role restriction to decision-authorized roles.
- Impact: analyst (and other non-client officers with ownership) can potentially finalize outcomes outside intended authority model.

### P0-2: High-risk approval control inconsistent across endpoints
- `POST /api/applications/:id/decision` blocks `co` approving `HIGH/VERY_HIGH`.
- `PATCH /api/applications/:id` approval path does not enforce equivalent co high-risk role prohibition.
- Impact: route inconsistency can weaken intended authority boundary.

## P1 (high)

### P1-1: No `submitted_to_compliance` status or endpoint
- Submit-to-compliance and approve are currently conflated as decision flow semantics; no dedicated handoff state exists.

### P1-2: UI authority signaling is ambiguous
- Core action buttons are visible in standard action bar broadly; some permissions fail only at click/submit.
- This can mislead analysts/junior roles about what they are actually authorized to do.

### P1-3: Memo approval UI permission mismatch
- UI checks `approve_low_medium` for memo approval button action, while backend memo approval is admin/sco only.
- Produces avoidable deny-after-click behavior.

### P1-4: Screening second-review protection is backend-only (good), but UI not role-tailored
- Backend correctly enforces second review as admin/sco only.
- UI still presents flows that can be attempted by unauthorized roles until submit.

## P2 (moderate/clarity)

### P2-1: Pre-approval decision docstring/status wording drift
- Comments mention `pre_approved` target, while implementation writes `kyc_documents` on pre-approve.
- Increases policy interpretation risk.

### P2-2: Role rename partially propagated
- Backend role label maps `co` to Onboarding Officer, but copy/actions across UI and permissions still mix legacy mental model terms.

## Controls explicitly preserved in design
- Screening second-review protection remains protected and must stay senior-only.
- EDD closure dual-control and senior closure requirements remain intact.
- Existing memo/document reliance gates remain final-approval controls.
