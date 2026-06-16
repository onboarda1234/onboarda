# PR-CR1R Full Suite Results

Status: passed.

Command:

```bash
cd arie-backend
pytest -q
```

Result on original PR head: `5430 passed, 17 skipped in 230.01s (0:03:50)`.

Result after rebasing onto current `origin/main` `5d30ab0b4af83b8d6272fda1840e25e985c92037`: `5434 passed, 17 skipped in 263.82s (0:04:23)`.

Follow-up corrective branch `codex/pr-cr1r-manual-country-defaults` initial implementation:

```bash
cd arie-backend
pytest -q
```

Result: `5435 passed, 17 skipped in 239.72s (0:03:59)`.

Follow-up corrective branch final one-time marker implementation:

```bash
cd arie-backend
pytest -q
```

Result: `5436 passed, 17 skipped in 236.40s (0:03:56)`.
