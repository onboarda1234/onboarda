# PR-CA2 Full Suite Results

Status: local full suite passed before the final lint-import amendment; amended local rerun blocked by native WeasyPrint/Pango CFFI segfault during collection. GitHub CI is required for final amended full-suite evidence.

## Backend Full Suite

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest
```

Result:

```text
5334 passed, 25 skipped, 4 subtests passed in 224.37s (0:03:44)
```

## Amended Branch Local Full Suite Rerun

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest
```

Result:

```text
Fatal Python error: Segmentation fault during collection while importing WeasyPrint/Pango CFFI through evidence_pack_export.py.
Exit code: 139.
```

Interpretation:

The failure occurred before test execution in the local native PDF dependency path, matching the known local WeasyPrint/Pango CFFI blocker. The amended code path is covered by targeted tests, closed-remediation regressions, frontend/static checks, py_compile, and the CI-equivalent flake8 selector. Final amended full-suite evidence must come from GitHub CI.

Known local constraint:

- System `python3` is Python 3.9.6 and is not compatible with this backend.
- Homebrew Python 3.11.15 is available and was used for targeted tests.
