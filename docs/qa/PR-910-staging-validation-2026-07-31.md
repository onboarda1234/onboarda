# PR #910 staging validation — baseline mismatch

Date: 2026-07-31  
Environment: AWS staging (`af-south-1`, ECS cluster `regmind-staging`)  
Requested commit: `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`  
Validation mode: read-only

## Verdict

**FAIL at Step 0 — deployed SHA mismatch.**

The backend and verification worker both run image
`b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`, not the requested PR #910
merge SHA `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`. In accordance with the
validation protocol, all later checks were stopped because results from a
different build cannot validate PR #910.

No pilot or production-readiness claim is made.

## Step 0 — Pin the baseline: FAIL

Observed at `2026-07-31T07:27:14Z`:

| Service | Task definition | Image tag | Running / desired | Rollout | Failed tasks |
|---|---|---|---:|---|---:|
| Backend | `regmind-staging:989` | `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8` | 2 / 2 | `COMPLETED` | 0 |
| Verification worker | `regmind-verification-worker:437` | `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8` | 6 / 6 | `COMPLETED` | 0 |

Both task definitions referenced:

`782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`

The deployed tag resolves to repository commit `b5fb2327`, “Merge pull request
#907 … PR-RELEASE-CONTROLS-1”, and is not PR #910.

### ALB and endpoint observations

- Backend target group `regmind-staging-tg/5e7928153c29b613`: two targets,
  `10.0.3.61:8080` and `10.0.4.203:8080`, both `healthy`.
- `GET /api/liveness`: HTTP 200, `status=ok`.
- `GET /api/health`: HTTP 200, `status=ok`, `environment=staging`.
- `GET /api/version`: HTTP 401 without an authenticated officer session.
- `GET /api/readiness`: HTTP 401 without an authenticated officer session.

The endpoint observations establish that the older deployment is serving, but
they do not cure the SHA mismatch. Authenticated version and readiness results
are **UNVERIFIED** because validation was required to stop on the mismatch.

## Steps 1–9

| Step | Result | Reason |
|---|---|---|
| 1 — Risk Assessment panel | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 2 — Zero contribution | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 3 — Bar arithmetic | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 4 — Cross-application bars | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 5 — Screen/export agreement | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 6 — Long driver behavior | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 7 — Cross-browser behavior | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 8 — Surrounding workflow | UNVERIFIED | Stopped after the mandatory SHA gate failed. |
| 9 — CloudWatch validation window | UNVERIFIED | No PR #910 validation window was opened against the wrong build. |

No screenshots or application references were collected because opening the
panel after the baseline failure would have tested an invalid build.

## Reproduction

1. Describe ECS services `regmind-backend` and
   `regmind-verification-worker` in cluster `regmind-staging`,
   region `af-south-1`.
2. Resolve their active task definitions.
3. Inspect each container image tag.
4. Compare each tag with
   `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`.
5. Observe that both instead equal
   `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`.

## Coverage and constraints

This was a read-only staging check. No application was actioned, no data or
configuration was changed, no deploy was initiated, and no production,
RSMP, Tier 0C-A, Tier 0C-B, recomputation, reseed, or direct SQL action was
performed. The frozen Application Review and Screening Queue workflows were
not touched.

Re-run the complete protocol only after both backend and worker image tags are
confirmed to equal the requested PR #910 merge SHA (or after an explicitly
approved new baseline is supplied).
