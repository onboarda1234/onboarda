# PR-6 Diagnosis

## Scope

- PR: `PR-6 - IDV Webhook and Runtime Baseline`
- Branch: `codex/pr6-idv-webhook-and-runtime-baseline`
- Base `origin/main` SHA: `b061c52f147b6fa42398629bb2b5dd2502682f3d`
- Prerequisites before start:
  - FSI-001: CLOSED
  - FSI-002: CLOSED
  - FSI-003: CLOSED
  - FSI-007 / PR-4B: CLOSED
  - FSI-005 / FSI-006 / PR-5: CLOSED

## FSI-011 - Sumsub Webhook Post-Commit Renormalization

Current-main diagnosis: OPEN.

Evidence:

- `SumsubWebhookHandler` commits webhook legacy writes, then closes `db` in `finally`.
- After the close, the post-commit renormalization block calls:

```python
webhook_renormalize_from_committed_legacy(db, app_id)
```

- The helper currently ignores the passed `legacy_db` and opens a fresh connection, so the current implementation often works by accident.
- The call contract is still unsafe because a closed DB handle is passed into a post-commit helper.
- Existing tests proved the committed-read invariant, but did not prove that the webhook never passes a closed handle.

## POST-INFRA - Worker / Runtime Baseline

Current staging diagnosis: OPEN.

Read-only AWS evidence:

- Backend service: `regmind-backend`
  - desired/running: `2/2`
  - task definition: `regmind-staging:560`
  - image tag: `b061c52f147b6fa42398629bb2b5dd2502682f3d`
  - env `GIT_SHA`: `b061c52f147b6fa42398629bb2b5dd2502682f3d`
  - env `IMAGE_TAG`: `b061c52f147b6fa42398629bb2b5dd2502682f3d`
- Verification worker service: `regmind-verification-worker`
  - desired/running: `6/6`
  - task definition: `regmind-verification-worker:9`
  - image tag: `15b281fa620d19c8a475f5d3e94e78edcf976f5a`
  - env `GIT_SHA`: `15b281fa620d19c8a475f5d3e94e78edcf976f5a`
  - env `IMAGE_TAG`: `15b281fa620d19c8a475f5d3e94e78edcf976f5a`

Conclusion:

- Backend runtime is aligned with merged main.
- Worker runtime is healthy but stale.
- Worker image/env provenance does not match current merged main.
- Existing staging deploy workflow updates only the backend ECS service, not the verification worker ECS service.

Evidence files:

- `runtime_json/staging_runtime_baseline_diagnosis_redacted.json`
- `runtime_json/staging_runtime_baseline_helper_prefix_redacted.json`
- `runtime_json/staging_worker_log_streams_redacted.json`
- `runtime_json/staging_worker_log_excerpt_redacted.json`

## Worker Handler Contract Diagnosis

Current-main diagnosis: OPEN.

Evidence:

- `verification_worker.default_verification_executor()` calls:

```python
DocumentVerifyHandler._post_with_db(
    handler,
    job["document_id"],
    SYSTEM_USER,
    db,
    force_sync=True,
    audit_actor_type="system",
    started_trigger="async_verify_worker_started",
    completed_trigger="async_verify_worker_completed",
    audit_detail_extra={"job_id": job["id"], "worker_id": worker_id},
    close_db=False,
)
```

- Current `DocumentVerifyHandler._post_with_db()` did not accept those keyword arguments.
- The existing worker test monkeypatched the handler and therefore did not exercise the real method contract.

Conclusion:

- The deployed worker could be running, but the real default worker execution path was not proven compatible with current handler code.
