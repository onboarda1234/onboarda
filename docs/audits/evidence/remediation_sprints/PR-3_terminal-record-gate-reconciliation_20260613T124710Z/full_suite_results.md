# Full Suite Results

## Local Full Relevant Backend Suite

Command:

```bash
python3.11 -m pytest arie-backend/tests -q --ignore=arie-backend/tests/test_pdf_generator.py
```

Final result:

- PASS — 5,265 passed, 17 skipped
- Duration: 386.11s
- Collected: 5,282 tests

This matches the relevant CI suite shape, which ignores `tests/test_pdf_generator.py`.

## Intermediate Failure And Resolution

An earlier full-suite attempt failed only in `test_case_command_centre_runtime.py` because the runtime harness did not include the new terminal Case Command Centre helper accessors. The product behavior was kept intact; the harness was updated and a new terminal-record runtime test was added.

After that patch:

- `python3.11 -m pytest arie-backend/tests/test_case_command_centre_runtime.py -q`
  - PASS — 28 passed
- Full relevant backend suite rerun:
  - PASS — 5,265 passed, 17 skipped

## Native PDF Dependency

The local full relevant suite did not hit the prior WeasyPrint/Pango CFFI segfault path because the same PDF generator test excluded by CI was ignored.
