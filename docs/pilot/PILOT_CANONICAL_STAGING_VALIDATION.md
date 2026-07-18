# Pilot Canonical Dataset — Demo-Completion Validation

Status: **Draft PR validation; no deployment or staging mutation performed**

This report records the read-only baseline and the validation boundary for the canonical demo-completion change. It does not claim pilot or production readiness.

## Pinned AWS staging baseline

| Item | Read-only result |
|---|---|
| `origin/main` and deployed SHA | `9a77e119275393ab10fe56c05d4971fe639b0c2f` |
| Backend | task definition `regmind-staging:872`; desired/running 2/2 |
| Worker | task definition `regmind-verification-worker:320`; desired/running 6/6 |
| Images and `/api/version` | `GIT_SHA` and `IMAGE_TAG` match the pinned SHA |
| Health | ECS rollouts complete; ALB 2/2 healthy; liveness, health and authenticated readiness HTTP 200 |
| Applications | 41 total; 41 `RM-PILOT-*`; zero noncanonical; zero duplicate references |
| Manifest SHA-256 | `fee7436a6bf6ead1cc9a8090ceaa3de7071a9b745e43f2c69a445cf74efdf9c9` |
| Risk configuration | `risk_config:2026-07-17 11:16:03.481284`; hash `97347127b940f0889c105c84323e7f465370fa1df1b38c9ad3a4cb3bd197b43c` |
| Approved values | Manufacturing 2; D3 40/35/25 |
| RSMP | environment override absent; runtime evaluation OFF |

## Demo-completion change

| Area | Draft implementation and evidence |
|---|---|
| Memo rendering | Deterministic fixture payload supplies the existing renderer's structured sections and pins authoritative score, tier, route, risk-config version, manifest hash and audit provenance. |
| Notifications | Authoritative `applications.is_fixture` suppresses delivery before dispatch or notification-state writes. The projection and structured log explain that delivery is intentionally suppressed. Nonfixture behavior is unchanged. |
| Monitoring | List/detail API projects the authoritative fixture flag; the Back Office labels canonical alerts `Pilot Canonical / Synthetic` and preserves stored severity/status. |
| Periodic Review | Canonical fixtures receive deterministic review dates and priority; open/completed views retain fixture labels. Viewing remains read-only and delivery remains suppressed. |
| AI Supervisor | Explicitly excluded from the controlled pilot. Stored synthetic evidence is retained for future development/testing, hidden as an active verdict, and cannot alter noncanonical governance. |

## Validation boundary

Focused unit, renderer, notification, monitoring, periodic-review, canonical, PostgreSQL, export and repository-policy checks are recorded in the draft PR and CI. The 41-scenario manifest is unchanged. The implementation contains no schema migration, risk-model/configuration change, provider invocation, notification delivery, recomputation, deployment or activation path.

Because this PR is intentionally not deployed or used to reseed staging, authenticated AWS browser validation of the changed memo, Monitoring and Periodic Review screens remains a separate post-deployment validation step. It must use the existing sanctioned fixture path and must not trigger provider refresh or notification delivery.

## Controlled scope

- AI Supervisor is excluded from this pilot and is not presented as pilot-ready.
- RSMP remains OFF and Tier 0C has not run.
- No staging record or risk configuration was changed by this exercise.
- No provider call, email, notification or webhook was sent.
- Production was not accessed.
- No pilot-readiness or production-readiness claim is made.
