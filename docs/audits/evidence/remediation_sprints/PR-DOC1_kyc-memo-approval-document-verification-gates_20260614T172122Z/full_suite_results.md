# PR-DOC1 Full Suite Results

Command:

```bash
/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest arie-backend/tests -q
```

Result:

```text
5346 passed, 25 skipped, 76 warnings in 222.85s (0:03:42)
```

Notes:

- Local full backend suite passed in the bundled Python 3.12 runtime after rebasing onto `origin/main` at `787ce4a26abfbaceaa043011df4e3f961fa4f418`.
- The local system Python is 3.9.6 and is not suitable for this backend suite because the repo uses Python 3.11+ syntax.
- PDF-related tests that require unavailable native WeasyPrint/Pango components were skipped by existing test conditions.
