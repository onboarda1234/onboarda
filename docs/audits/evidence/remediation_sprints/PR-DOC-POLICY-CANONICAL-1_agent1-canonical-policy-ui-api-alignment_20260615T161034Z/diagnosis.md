# PR-DOC-POLICY-CANONICAL-1 Diagnosis

Base source of truth: `origin/main` at `ecf2a607dab12257d21f623b3a8e3a8af221ad43`.

Diagnosis result: Agent 1 had two overlapping product models. Runtime verification was still driven by the existing onboarding-focused verification matrix and `ai_checks` seed rows, while PR-DOC2A introduced a wider lifecycle settings registry that made EDD, change management, periodic review, monitoring, and regulatory evidence visible in settings. That widened the settings surface faster than the executable backend coverage.

Current-state gap:

- Runtime executable checks are document-type based in `verification_matrix.py` and seed checks, but there was no backend API payload that explicitly separated active runtime policies from manual-review-only and future/enterprise policy rows.
- Workflow usage and blocker/trigger behaviour was mixed into lifecycle settings copy instead of being a separate mapping layer.
- Accepted upload aliases such as `id_card`, `drivers_license`, `director_id`, and `ubo_id` were accepted but did not clearly map to one canonical National ID policy in both the backend registry and upload normalization.
- Back-office settings still used legacy "AI Verification Checks" framing and could imply lifecycle rows were equivalent to runtime-verified policies.
- Agent 1 pipeline copy could be read as broader than document integrity and needed a clear boundary excluding approval/rejection/waiver authority and sanctions/PEP/adverse-media screening.

Scope discipline:

- No SAR/STR implementation was added.
- No broad enterprise monitoring/regulatory runtime checks were activated.
- No approval, EDD, periodic review, or change-management enforcement gates were weakened.
- This PR creates a truthful canonical registry and UI/API alignment layer. It does not close unrelated remediation items.

