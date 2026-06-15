# PR-CA4 Full Suite Results

## Command

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests
```

## Result

```text
Initial PR run before CodeRabbit follow-up:
5371 passed, 25 skipped, 4 subtests passed in 259.81s (0:04:19)

Post-CodeRabbit local rerun:
Blocked by native WeasyPrint/Pango CFFI segmentation fault during pytest collection before tests executed.
```

## Notes

- The full backend suite passed with Python 3.11 before the CodeRabbit follow-up commit.
- An earlier broad attempt with the system Python 3.9 interpreter failed before meaningful test execution because current `origin/main` imports Python 3.10+ union type syntax. That environment is not a valid runner for this repository revision.
- After CodeRabbit follow-up fixes, the focused PR-CA4 tests and CA regression subset passed. The attempted full local rerun was blocked by the known WeasyPrint/Pango CFFI native segfault; GitHub CI full-suite evidence is required before merge.
