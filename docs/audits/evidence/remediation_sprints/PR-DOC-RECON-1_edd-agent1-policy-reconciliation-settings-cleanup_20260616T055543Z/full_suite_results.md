# Full Suite Results

Command:

```bash
cd arie-backend && /opt/homebrew/bin/python3.11 -m pytest -q
```

Result:

```text
5434 passed, 17 skipped in 275.85s (0:04:35)
```

Notes:

- Python 3.11 was used because `arie-backend/pyproject.toml` declares `requires-python = ">=3.11"`.
- The suite covers existing portal upload tests, PR-DOC-UI-1 tests, PR-DOC-POLICY-CANONICAL-1 tests, DOC2A tests, EDD/enhanced requirement tests, approval gates, monitoring, periodic review, document upload/storage/versioning, and verification matrix regressions.

## GitHub CI

PR: https://github.com/onboarda1234/onboarda/pull/504

Run: https://github.com/onboarda1234/onboarda/actions/runs/27598901657

Result:

```text
CodeRabbit: passed
lint-and-test: passed (15m13s)
pdf-tests: passed (35s)
docker-validate: passed (53s)
```
