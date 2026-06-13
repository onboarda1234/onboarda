# PR-6 Targeted Test Results

## Compile / Diff

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile \
  arie-backend/server.py \
  arie-backend/screening_storage.py \
  arie-backend/verification_worker.py \
  arie-backend/verification_jobs.py \
  arie-backend/scripts/staging_runtime_baseline.py \
  arie-backend/scripts/verification_worker_smoke.py \
  arie-backend/tests/test_webhook_normalized_upsert.py \
  arie-backend/tests/test_pr7a_async_verification_worker_runtime.py \
  arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py
```

Result: PASS.

Command:

```bash
git diff --check
```

Result: PASS.

Command:

```bash
/opt/homebrew/bin/python3.11 - <<'PY'
from pathlib import Path
import yaml
yaml.safe_load(Path(".github/workflows/deploy-staging.yml").read_text())
print("YAML parse PASS")
PY
```

Result: PASS.

## Focused PR-6 Tests

Command:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest -q \
  tests/test_webhook_normalized_upsert.py \
  tests/test_pr7a_async_verification_worker_runtime.py \
  tests/test_pr6_idv_webhook_runtime_baseline.py
```

Result:

```text
25 passed in 2.92s
```

Coverage:

- FSI-011 webhook renormalization call no longer passes DB handles.
- Renormalization committed-read behavior remains intact.
- Webhook idempotency remains intact.
- Renormalization operational failures remain non-blocking and PII-safe.
- Real worker default executor can call `DocumentVerifyHandler._post_with_db()`.
- Runtime baseline helper detects stale worker image.
- Runtime baseline helper redacts secret values.
- Deploy workflow updates worker task definition/service.
- Synthetic worker smoke processes a job without provider calls.

## Broad IDV / Worker / Sumsub Tests

Command:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest -q \
  tests/test_webhook_normalized_upsert.py \
  tests/test_sumsub_hardening_pr14.py \
  tests/test_sumsub_dual_sig.py \
  tests/test_sumsub_verification.py \
  tests/test_sumsub_integrity_hardening.py \
  tests/test_sumsub_level_split.py \
  tests/test_sumsub_409_retry.py \
  tests/test_idv_approval_gate.py \
  tests/test_kyc_1a_sumsub_idv_visibility.py \
  tests/test_pr6_async_verification_foundation.py \
  tests/test_pr7a_async_verification_worker_runtime.py \
  tests/test_pr6_idv_webhook_runtime_baseline.py \
  tests/test_pr6_observability_baseline.py
```

Result:

```text
206 passed in 3.96s
```

## Closed-Remediation Regression Subset

Command:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest -q \
  tests/test_pr1_client_api_boundary.py \
  tests/test_pr1b_client_notification_boundary.py \
  tests/test_sprint35.py::TestLogout \
  tests/test_pr3_terminal_record_gate_reconciliation.py \
  tests/test_pr4_screening_memo_readiness_metadata.py \
  tests/test_pr5_memo_governance.py
```

Result:

```text
39 passed in 7.54s
```
