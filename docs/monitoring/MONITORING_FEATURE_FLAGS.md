# Monitoring Feature Flags

## Purpose

`PR-MON-FEATURE-FLAGS-1` establishes governance switches for future Monitoring
work without implementing or activating any Monitoring workflow. The existing
environment configuration mechanism evaluates the flags, exposes a read-only
operator status through `GET /api/config/environment`, and records their state
once during startup.

This framework does not change Applications, Screening Queue, Screening
Review, RSMP, KYC & Documents, EDD, Change Management, or the Portal.

## Flags

| Flag | Default | Future consumer |
|---|---:|---|
| `ENABLE_DOCUMENT_RENEWAL_AUTOMATION` | OFF | Document Renewal |
| `ENABLE_AGENT1_REFRESH_VERIFICATION` | OFF | Agent 1 |
| `ENABLE_MONITORING_SCREENING_CHANGE` | OFF | Screening Monitoring |
| `ENABLE_MONITORING_AUTO_RESOLUTION` | OFF | Automatic Closure |

Every flag defaults to OFF in local development, testing, demo, staging, and
production. A missing or unrecognised value evaluates OFF. Recognised explicit
ON values are `true`, `1`, `yes`, and `on`; evaluation alone does not execute a
workflow because this PR deliberately introduces no consumer.

## Activation policy

1. Keep every flag OFF after this PR.
2. Activate a flag only in the environment targeted by its approved future PR.
3. Require that PR to implement the guarded workflow, rollback procedure,
   protected regression comparison, staging validation, and operator evidence.
4. Never use a code default of ON.
5. Treat an unknown value as OFF and correct the configuration before
   activation.

## Operator visibility

`GET /api/config/environment` includes `monitoring_feature_flags`, a read-only
status block. Each entry reports its label, evaluated boolean, and `ON`/`OFF`
state. The endpoint provides no activation, edit, save, or publish control.

Startup emits one `Monitoring Feature Flags` log record containing the four
evaluated states. It does not log repeatedly during request processing.

## State after this PR

All four Monitoring feature flags remain **OFF**. No Monitoring feature,
automation, automatic resolution, document renewal, Agent 1 refresh, or
screening-change workflow is activated or implemented by this PR.
