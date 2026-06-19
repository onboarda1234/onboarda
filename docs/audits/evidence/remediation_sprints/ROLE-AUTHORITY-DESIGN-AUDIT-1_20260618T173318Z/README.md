# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Evidence Folder

Deep, code-grounded audit + design of RegMind's role authority model.
**Source of truth:** `origin/main` @ `69effaa`. **Audit/design only — no code, data, or settings changed.**

## Deliverables (11)
| File | Purpose |
|---|---|
| `current_role_matrix.md` | Observed role capabilities today, with `file:line` enforcement |
| `target_role_matrix.md` | Target authority model + admin policy + deltas |
| `endpoint_authority_matrix.md` | Backend endpoint × action × role gate (decision/EDD/screening/override/config) |
| `button_visibility_matrix.md` | Back-office button visibility vs backend authority; portal neutrality check |
| `status_transition_matrix.md` | Status set + transition map + endpoints; `submitted_to_compliance` gap |
| `approval_gate_matrix.md` | Every approval gate, tagged blocks-approval? / blocks-submission? |
| `submit_to_compliance_design.md` | Design for the missing handoff status/endpoint/queue/UI |
| `audit_trail_requirements.md` | Audit infra, event vocabulary, reconstruction gaps |
| `bypass_risk_findings.md` | Code-grounded bypass findings (P0/P1/P2) + sound controls |
| `recommended_pr_sequence.md` | 5-PR implementation plan, authority-gate-first (design only) |
| `summary.json` | Machine-readable rollup |

## Supersedes
These supersede the thin root-level files committed in `1768037` (PR #532):
`current_role_matrix.md`, `target_role_matrix.md`, `endpoint_authority_matrix.md`,
`button_visibility_matrix.md`, `status_transition_matrix.md`, `submit_to_compliance_design.md`,
`recommended_pr_sequence.md`, `summary.json`, `audit_findings.md`. The three safety-critical
deliverables (`approval_gate_matrix.md`, `audit_trail_requirements.md`, `bypass_risk_findings.md`)
were missing entirely and are created here.

## Headline
- **P0-1 (pilot-blocking):** `PATCH /api/applications/:id` can finalize `approved`/`rejected` with bare
  `require_auth()`, omitting the co-cannot-approve-HIGH actor gate and dual-approval that `/decision` enforces.
- **P1-2 (pilot-blocking):** no `submitted_to_compliance` status/endpoint/button exists — officers have no
  correct forward action on high-risk cases (dead-end).
- Sound controls (do not weaken): screening four-eyes, EDD dual-control closure, override/waiver senior-only,
  HIGH/VH dual-approval on `/decision`, current-risk evaluation, portal neutrality.
