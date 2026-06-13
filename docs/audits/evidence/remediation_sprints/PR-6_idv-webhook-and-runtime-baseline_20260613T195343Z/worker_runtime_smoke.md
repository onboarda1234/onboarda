# PR-6 Worker Runtime Smoke

Branch-stage local worker smoke is covered by tests:

- `tests/test_pr7a_async_verification_worker_runtime.py`
- `tests/test_pr6_idv_webhook_runtime_baseline.py`

Local synthetic smoke behavior:

- seeds a clearly named synthetic application and document;
- enqueues a verification job;
- claims it through the production queue claim function;
- processes it through `verification_worker.process_claimed_job()`;
- uses a synthetic executor, so it does not call Sumsub, ComplyAdvantage, Anthropic, S3, email, payment, or other live providers;
- verifies job terminal state and document compatibility state;
- supports cleanup of synthetic staging rows.

Post-merge required staging command, to run inside the deployed backend/worker task environment:

```bash
python arie-backend/scripts/verification_worker_smoke.py \
  --run-id pr6smoke \
  --worker-id pr6-smoke \
  --cleanup
```

POST-INFRA must remain `PARTIALLY FIXED` until this smoke or an equivalent safe worker job smoke passes on merged-main staging.
