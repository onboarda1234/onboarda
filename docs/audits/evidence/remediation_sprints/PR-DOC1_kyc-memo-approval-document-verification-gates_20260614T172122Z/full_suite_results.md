# PR-DOC1 Full Suite Results

Command:

```bash
/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest arie-backend/tests -q
```

Result:

```text
5346 passed, 25 skipped, 76 warnings in 199.59s (0:03:19)
```

Notes:

- Local full backend suite passed in the bundled Python 3.12 runtime.
- The local system Python is 3.9.6 and is not suitable for this backend suite because the repo uses Python 3.11+ syntax.
- PDF-related tests that require unavailable native WeasyPrint/Pango components were skipped by existing test conditions.

