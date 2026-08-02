# Monitoring Feature Flags

## Purpose

`PR-MON-FEATURE-FLAGS-1` established governance switches for future Monitoring
work without activating any Monitoring workflow. The existing
environment configuration mechanism evaluates the flags, exposes a read-only
operator status through `GET /api/config/environment`, and records their state
once during startup.

This framework does not change Applications, Screening Queue, Screening
Review, RSMP, KYC & Documents, EDD, Change Management, or the Portal.

## Flags

| Flag | Default | Future consumer |
|---|---:|---|
| `ENABLE_DOCUMENT_RENEWAL_AUTOMATION` | OFF | Document Renewal request workflow (`monitoring_document_renewal_request_v1`) |
| `ENABLE_AGENT1_REFRESH_VERIFICATION` | OFF | Agent 1 |
| `ENABLE_MONITORING_SCREENING_CHANGE` | OFF | Screening Monitoring |
| `ENABLE_MONITORING_AUTO_RESOLUTION` | OFF | Automatic Closure |

Every flag defaults to OFF in local development, testing, demo, staging, and
production. A missing or unrecognised value evaluates OFF. Recognised explicit
ON values are `true`, `1`, `yes`, and `on`. The Document Renewal request
service is the only approved consumer introduced by
`PR-MON-DOC-RENEWAL-REQUEST-1`; it gates every new renewal mutation and reminder
or eligibility tick. Authenticated read projections remain available while the
flag is OFF so existing request evidence does not disappear. The artifact
cleanup reconciler is intentionally flag-independent: turning the feature OFF
must stop new work without abandoning already-stored, unattached candidate
files. The other three flags still have no business-workflow consumer.

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

## State after PR-MON-DOC-RENEWAL-REQUEST-1

All four Monitoring feature flags remain **OFF** after deployment. The Document
Renewal request workflow exists but is inactive until a separately approved
environment activation. Agent 1 refresh, automatic resolution and
screening-change workflows remain unimplemented and inactive. No default,
startup path, migration, or scheduler enables a flag.
