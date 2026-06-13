# PR-1B Full Suite Results

## Local Full Backend Suite

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
5253 passed, 25 skipped in 273.13s (0:04:33)
```

Verdict:

`PASS`

This local run did not reproduce the prior PR-1 WeasyPrint/Pango CFFI segmentation fault.

## GitHub CI

Pending. GitHub CI evidence must be recorded after the PR is opened and checks complete.
