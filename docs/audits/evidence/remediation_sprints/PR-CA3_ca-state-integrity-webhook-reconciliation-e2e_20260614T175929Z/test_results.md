# PR-CA3 Targeted Test Results

## Compile

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile arie-backend/screening_state.py arie-backend/server.py arie-backend/db.py arie-backend/security_hardening.py arie-backend/screening_complyadvantage/webhook_storage.py arie-backend/screening_complyadvantage/webhook_handler.py arie-backend/screening_complyadvantage/client.py arie-backend/tests/test_complyadvantage_runtime_e2e.py arie-backend/tests/test_approval_gate.py arie-backend/tests/test_complyadvantage_client.py arie-backend/tests/test_complyadvantage_webhook_handler.py arie-backend/tests/test_complyadvantage_webhook_storage.py arie-backend/tests/test_screening_queue.py arie-backend/tests/test_screening_state_priority_a.py
```

Result:

```text
PASS
```

## Diff Check

Command:

```bash
git diff --check
```

Result:

```text
PASS
```

## PR-CA3 Focused / Affected Set

Command:

```bash
PYTHONPATH=arie-backend /opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_screening_freshness.py \
  arie-backend/tests/test_screening_review.py \
  arie-backend/tests/test_complyadvantage_runtime_e2e.py \
  arie-backend/tests/test_screening_state_priority_a.py \
  arie-backend/tests/test_screening_queue.py \
  arie-backend/tests/test_complyadvantage_webhook_storage.py \
  arie-backend/tests/test_complyadvantage_webhook_handler.py \
  arie-backend/tests/test_complyadvantage_client.py \
  arie-backend/tests/test_approval_gate.py \
  -q
```

Result:

```text
201 passed in 2.25s
```

Note: after merged PR #491 staging smoke found CA truth/gate drift for material input changes after screening, corrective branch `codex/pr-ca3-corrective-input-staleness` added:

- `test_runtime_e2e_material_input_change_marks_ca_truth_stale`
- `test_runtime_e2e_approval_gate_blocks_material_input_change_after_ca_screening`

Corrective focused compile/diff check:

```text
py_compile touched Python files: PASS
git diff --check: PASS
```

Corrective focused set:

```text
86 passed in 1.73s
```

## Closed-Control Regression Subset

Command:

```bash
PYTHONPATH=arie-backend /opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_screening_config.py \
  arie-backend/tests/test_screening_provider.py \
  arie-backend/tests/test_provider_label_policy.py \
  arie-backend/tests/test_complyadvantage_payloads.py \
  arie-backend/tests/test_complyadvantage_orchestrator.py \
  arie-backend/tests/test_complyadvantage_evidence_audit.py \
  arie-backend/tests/test_complyadvantage_evidence_backfill.py \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py \
  arie-backend/tests/test_pr1b_client_notification_boundary.py \
  arie-backend/tests/test_auth_stability.py \
  arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py \
  arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py \
  arie-backend/tests/test_pr5_memo_governance.py \
  arie-backend/tests/test_pr5b_memo_concision.py \
  arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py \
  arie-backend/tests/test_pr6_async_verification_foundation.py \
  arie-backend/tests/test_pr6_observability_baseline.py \
  -q
```

Result:

```text
205 passed in 7.57s
```
