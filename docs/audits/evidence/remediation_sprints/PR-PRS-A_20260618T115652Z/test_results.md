# PR-PRS-A Test Results

Executed from `/Users/Aisha/CodexWork/onboarda-pr-prs-a/arie-backend` on Python 3.11.

## Targeted Periodic Review Suite

Command:

```bash
python3.11 -m pytest tests/test_periodic_review_engine.py tests/test_periodic_review_handlers.py tests/test_periodic_review_queue_hygiene.py -v
```

Result:

```text
============================= 129 passed in 29.39s =============================
```

Raw log: `logs/pytest_periodic_review_targeted.log`

## Full Backend Suite

Command:

```bash
python3.11 -m pytest tests/ -q
```

Result:

```text
================= 5542 passed, 17 skipped in 320.16s (0:05:20) =================
```

Raw log: `logs/pytest_full.log`
