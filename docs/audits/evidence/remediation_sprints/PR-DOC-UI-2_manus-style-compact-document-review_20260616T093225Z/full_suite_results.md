# Full Suite Results

Command:

```bash
cd arie-backend
/opt/homebrew/bin/python3.11 -m pytest -q
```

Result:

```text
5447 passed, 17 skipped in 244.36s (0:04:04)
```

Notes:

- This suite includes document upload/storage, document reliance gates, portal ownership, upload latency, PR-DOC2A, PR-DOC-UI-1, PR-DOC-RECON-1, canonical policy registry, approval gate, memo, screening, EDD, and periodic review regressions.
- The run used the local generated `JWT_SECRET` fallback emitted by test startup.
