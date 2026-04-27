# PR Ledger

## Upload Latency Remediation

| Slot | Concern | Status | Notes |
| --- | --- | --- | --- |
| PR 0 | Governance playbook and memory skeleton | In progress | No runtime code. |
| PR 1 | Flag foundation and exposure contract | Planned | All new flags default off. |
| PR 2 | Upload/verify contract tests | Planned | Includes GATE-03 and audit event shape. |
| PR 3 | Telemetry and CloudWatch query prep | Planned | Must include query material. |
| PR 4 | BO polling slowdown | Planned | Behind `FF_POLLING_SLOW`. |
| PR 5 | BO 10 MB client-side rejection | Planned | Behind `FF_SIZE_CAP_CLIENT_REJECT`. |
| PR 5.5 | Stabilization window | Planned | 48-72h soak, no risky merges. |

## Gotchas

- `docs/IMPLEMENTATION_PLAYBOOK.md` existed locally before this work but was not
  present on GitHub `main`.
- Backend upload limit is already 10 MB on GitHub `main`.
- Back office client-side size check still references 25 MB before PR 5.
- Portal and back office currently chain upload and verification.
