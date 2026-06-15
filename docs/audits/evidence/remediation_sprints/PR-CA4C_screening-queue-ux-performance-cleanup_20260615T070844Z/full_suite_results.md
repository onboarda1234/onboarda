# PR-CA4C Full Suite Results

## Local Backend Suite

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
5382 passed, 25 skipped in 204.23s (0:03:24)
```

Notes:

- The full backend suite was run after the backend and frontend queue behavior changes.
- A final targeted rerun of the modified/static/provider/queue tests was run after the last UI placeholder-width adjustment and passed: `61 passed in 1.70s`.

## GitHub CI

Status:

- Pending. CI will be recorded after the PR is opened and checks complete.

