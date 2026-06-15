# PR-CA4 Full Suite Results

## Command

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests
```

## Result

```text
5371 passed, 25 skipped, 4 subtests passed in 259.81s (0:04:19)
```

## Notes

- The full backend suite passed with Python 3.11.
- An earlier broad attempt with the system Python 3.9 interpreter failed before meaningful test execution because current `origin/main` imports Python 3.10+ union type syntax. That environment is not a valid runner for this repository revision.
