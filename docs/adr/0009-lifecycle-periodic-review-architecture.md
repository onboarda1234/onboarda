# ADR-0009: Lifecycle / Periodic Review Architecture Freeze

## Status

Accepted

## Date

2026-05-19

## Context

Phase 1 established `periodic_reviews` as the canonical source of
periodic-review state, introduced shared projection services, and fenced
legacy decision behavior away from the new completion flow.

The next roadmap phases add officer-facing Lifecycle UI. Without an explicit
architecture freeze, RegMind risks reintroducing the exact problems Phase 1
was intended to remove:

- duplicate periodic-review state across multiple surfaces
- duplicate workflows inside Lifecycle, Ongoing Monitoring, or Case Management
- duplicate document storage outside the main KYC document repository
- AI outputs drifting into officer-owned judgment fields
- review completion logic spreading into queue surfaces instead of staying on
  the canonical review record

This ADR freezes the target architecture before UI work starts.

## Decision

### 1. Lifecycle owns periodic review only

Lifecycle is the client-level post-onboarding command centre.
Lifecycle owns the periodic-review workspace only.

Lifecycle must not become the owner workflow for:

- Screening Queue
- EDD
- Change Management
- KYC Documents
- Ongoing Monitoring alerts or agents
- Case Management allocation

Lifecycle may consume and deep-link to those modules, but may not duplicate
their workflow state, decision paths, or persistence.

### 2. Application detail Lifecycle tab is the officer workspace

The actual officer workspace for periodic review lives in the
Application Detail `Lifecycle` tab.

Other surfaces remain projections or launchpads:

- `Applications` is the portfolio/application/client list
- `Case Management` is assigned work only
- `Screening Queue` is screening-specific work
- `Ongoing Monitoring` is portfolio monitoring signals and monitoring agents
- `Lifecycle Queue` is the portfolio lifecycle launchpad

No separate standalone Periodic Review tab is introduced.

### 3. Canonical periodic-review state lives on `periodic_reviews`

Periodic-review status, assignment, setup, attestations, readiness, outcome,
memo linkage, and linked workflow references live on the periodic review
record or on review-owned link tables.

Other surfaces must project periodic-review state from the shared backend
projection service. They must not copy or denormalize review status into
other tables as a second source of truth.

### 4. Lifecycle is an orchestrator, not a duplicate workflow engine

Lifecycle consumes signals and linked state from owner workflows, including:

- KYC Documents and verifications
- Screening freshness and screening outcomes
- Ongoing Monitoring alerts
- EDD cases
- Change Management requests
- document health signals
- monitoring agent signals

Lifecycle may show blockers, context panels, status chips, and deep links.
Lifecycle must not replicate:

- screening review actions
- EDD case execution
- change-request approval flow
- KYC document repository ownership
- monitoring queue ownership

### 5. No duplicate document store

Periodic review evidence must use the existing document repository.
Evidence association is link-based, using review-level link records.

The architecture explicitly forbids:

- a separate periodic-review upload bucket
- a separate periodic-review documents table acting as a second repository
- copying existing document blobs or rows solely to attach them to a review

### 6. Officer judgment remains officer-owned

AI agents and system automation may surface facts, gaps, and signals.
They may not write officer-judgment fields.

The following fields remain human-owned:

- `material_change_attestation`
- `material_change_categories`
- `officer_rationale`
- `outcome`
- review completion state
- memo conclusion

The system may assemble deterministic facts for memo generation, but no
AI-authored officer conclusion may be inserted automatically.

### 7. Operational blockers and completion blockers stay explicit

Lifecycle must distinguish:

- operational blockers owned by linked workflows
- completion blockers owned by the periodic-review completion contract

A review may be due and incomplete without being operationally blocked.
Queue and detail surfaces must not collapse all incomplete work into
`Blocked`.

### 8. Lifecycle Queue is a launchpad, not an editor

Lifecycle Queue remains the portfolio-level launchpad into lifecycle work.
It may summarize canonical periodic-review state and link into the
Application Detail `Lifecycle` tab.

It must not become a second completion surface or a second periodic-review
editor.

### 9. Case Management is assigned work, not review state ownership

Case Management may project assigned periodic-review work from canonical
review state, alongside other assigned work types.

It must not own periodic-review workflow state and must not require a second
review-specific state machine.

## Implementation Boundaries

The phased implementation after this ADR follows these boundaries:

1. Add the `Lifecycle` tab shell inside application detail.
2. Build the current periodic-review workspace inside that tab.
3. Add evidence-linking UI backed by the existing document repository.
4. Add deep links to owner workflows without duplicating them.
5. Add memo gating and officer rationale/outcome UI.
6. Clean up Case Management so it shows assigned work only.
7. Clean up Ongoing Monitoring so it remains signal-focused.
8. Make Lifecycle Queue a deep-link launchpad, not an editor.
9. Integrate agent signals as signals only.
10. Add an authenticated staging E2E harness and complete end-to-end
    validation.
11. Align reports and analytics to canonical review state.
12. Complete production-readiness hardening.

Every phase must preserve the existing canonical-state rule and must stop
for a focused hotfix if validation finds a P0, P1, or P2 defect.

## Non-goals

This ADR rejects the following approaches:

- adding a separate periodic-review tab outside Application Lifecycle
- building duplicate Screening, EDD, or Change Management workflows inside
  Lifecycle
- creating a separate periodic-review document repository
- letting queues complete or own periodic reviews
- allowing AI agents to write officer judgment or completion decisions
- copying periodic-review status into other tables as a second source of truth

## Verification Principles

Every implementation PR after this ADR must validate against the same
principles:

1. GitHub `main` is the code source of truth.
2. AWS staging is the runtime source of truth after merge.
3. The deployed SHA must match merged `main` before runtime validation
   passes.
4. Shared periodic-review projections must remain consistent across all read
   surfaces.
5. UI surfaces may render or deep-link review state, but may not mutate
   unrelated owner workflows.
6. Review completion must remain canonical on the periodic review record.
7. Audit evidence must exist for mutating review actions.
8. Browser smoke on AWS staging is mandatory for UI PRs.
9. P0/P1/P2 validation defects block the roadmap until a focused hotfix
   lands and is revalidated.

## Consequences

- Positive: UI work can proceed without reopening core architecture debates.
- Positive: RegMind gets one review workspace, one state owner, and one
  document repository.
- Positive: regulator-facing evidence remains easier to reason about because
  ownership boundaries stay explicit.
- Negative: some existing surfaces will temporarily remain mixed during the
  transition until their cleanup phases land.
- Negative: future convenience requests that imply duplicate state or
  duplicate workflow actions must be rejected, even if they appear faster in
  the short term.

## Alternatives Considered

- **Keep periodic review editable from multiple surfaces**: Rejected because
  it recreates projection drift and unclear state ownership.
- **Create a dedicated Periodic Review module**: Rejected because Lifecycle is
  the intended client-level post-onboarding command centre.
- **Let AI agents pre-fill officer conclusions**: Rejected because it weakens
  human accountability and regulator-defensible auditability.
