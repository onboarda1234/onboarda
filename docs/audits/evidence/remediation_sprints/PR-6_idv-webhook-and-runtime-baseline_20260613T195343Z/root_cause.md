# PR-6 Root Cause

## FSI-011

The webhook renormalization helper had a misleading signature:

```python
webhook_renormalize_from_committed_legacy(legacy_db, application_id)
```

The helper opened a fresh committed-read connection internally and ignored
`legacy_db`, but the webhook call site still passed the legacy webhook write
connection after it had been closed.

Root cause:

- stale helper contract from an earlier implementation;
- missing regression proving the post-commit helper receives only the application identifier;
- a call site that allowed a closed DB handle to cross the transaction boundary.

## POST-INFRA

The backend and worker are separate ECS services, but `.github/workflows/deploy-staging.yml`
only registered and deployed a new task definition for `regmind-backend`.

Root cause:

- backend deploy automation advanced the API image to each merged main SHA;
- verification worker task definition/service was left outside the deploy path;
- runtime baseline validation was not automated as a strict read-only check;
- the worker service could be healthy while still running an old SHA.

## Worker Handler Runtime

The worker was designed to reuse the synchronous document verification handler,
but the real handler signature had not been updated to accept the worker runtime
arguments.

Root cause:

- existing worker test mocked the handler path and asserted the intended call shape;
- no test called the real `DocumentVerifyHandler._post_with_db()` through
  `default_verification_executor()`;
- the handler hardcoded user audit triggers and DB close behavior.
