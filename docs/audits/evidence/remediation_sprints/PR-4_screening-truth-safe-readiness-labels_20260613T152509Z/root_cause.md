# Root Cause

The screening truth model conflated provider/process terminality with approval readiness.

Exact causes:

- `build_screening_truth_summary()` set `approval_ready` to true for any terminal `completed_clear` or `completed_match`.
- An unresolved `completed_match` also produced `approval_blocking=true`, creating a contradictory API state.
- The back-office JavaScript fallback duplicated the same unsafe rule for `completed_match`.
- Case Command Centre and application blocker logic used the legacy `approval_ready` field rather than explicit blocker semantics.

The safe model requires separate fields:

- `screening_terminal`: the provider/process reached a terminal state.
- `screening_provider_clear`: a live provider returned terminal no-hit.
- `defensible_clear`: no unresolved material hit remains, including formally officer-cleared false positives with evidence.
- `screening_gate_ready`: screening-specific gate can proceed.
- `approval_blocked_reasons`: explicit reasons screening still blocks approval.

The legacy `approval_ready` field remains only as a backwards-compatible alias for `screening_gate_ready`; it must not be true when blockers remain.
